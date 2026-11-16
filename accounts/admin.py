import csv
import io

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone

from . import services
from .forms import ApprovedResidentCSVForm
from .models import ApprovedResident, User


class AwaitingPINFilter(admin.SimpleListFilter):
    """Surfaces verified residents who still need a lockbox PIN (UC-02 step 6)."""

    title = "ПИН код"
    parameter_name = "pin"

    def lookups(self, request, model_admin):
        return [("awaiting", "Чака ПИН код"), ("assigned", "Назначен ПИН код")]

    def queryset(self, request, queryset):
        from access_codes.models import LockboxPIN

        assigned = queryset.filter(lockbox_pins__status=LockboxPIN.Status.ASSIGNED)
        if self.value() == "assigned":
            return assigned
        if self.value() == "awaiting":
            return queryset.filter(
                is_active=True, email_verified=True, deactivated_at__isnull=True
            ).exclude(pk__in=assigned.values("pk"))
        return queryset


class SafeguardedUserChangeForm(UserChangeForm):
    """Blocks edits that would deactivate or demote the last active admin."""

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            services.check_admin_change(
                self.instance,
                is_active=cleaned.get("is_active", self.instance.is_active),
                is_staff=cleaned.get("is_staff", self.instance.is_staff),
                is_superuser=cleaned.get("is_superuser", self.instance.is_superuser),
            )
            if cleaned.get("is_active") and self.instance.deactivated_at is not None:
                # Reactivation via the change form must not bypass the
                # approved-resident eligibility rules.
                services.check_can_reactivate(self.instance)
        return cleaned


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Email-login variant of the stock UserAdmin (no username field)."""

    form = SafeguardedUserChangeForm
    ordering = ["email"]
    list_display = [
        "email",
        "first_name",
        "last_name",
        "is_active",
        "email_verified",
        "deactivated_at",
        "is_staff",
        "pin_status",
    ]
    list_filter = ["is_active", "email_verified", "is_staff", "is_superuser", AwaitingPINFilter]
    search_fields = ["email", "first_name", "last_name"]
    actions = ["deactivate_users", "reactivate_users", "allocate_pins"]
    readonly_fields = ["deactivated_by", "last_login", "date_joined"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Лични данни", {"fields": ("first_name", "last_name")}),
        (
            "Права",
            {
                "fields": (
                    "is_active",
                    "email_verified",
                    "deactivated_at",
                    "deactivated_by",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Важни дати", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "password1", "password2"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        deactivating = False
        if change:
            previous = User.objects.get(pk=obj.pk)
            deactivating = previous.is_active and not obj.is_active
            if deactivating and obj.deactivated_at is None:
                obj.deactivated_at = timezone.now()
                obj.deactivated_by = request.user
            elif obj.is_active:
                obj.deactivated_at = None
                obj.deactivated_by = None
        super().save_model(request, obj, form, change)
        if deactivating:
            # Keep the change-form path consistent with services.deactivate_user:
            # flag the PIN for physical removal, cancel pending deliveries, and
            # cancel future reservations.
            services.finalize_deactivation(obj, actor=request.user)

    def has_delete_permission(self, request, obj=None):
        allowed = super().has_delete_permission(request, obj)
        if allowed and obj is not None:
            try:
                services.check_admin_delete(obj)
            except ValidationError:
                return False
        return allowed

    @admin.display(description="ПИН код")
    def pin_status(self, obj: User) -> str:
        from access_codes.services import user_assigned_pin

        if user_assigned_pin(obj) is not None:
            return "Назначен"
        if obj.is_active and obj.email_verified and obj.deactivated_at is None:
            return "Чака"
        return "—"

    @admin.action(description="Назначи ПИН кодове на избраните потребители")
    def allocate_pins(self, request, queryset):
        from access_codes.services import allocate_pin_for_user, user_assigned_pin

        allocated = skipped = no_inventory = 0
        for user in queryset:
            if user_assigned_pin(user) is not None:
                skipped += 1
                continue
            pin = allocate_pin_for_user(user, actor=request.user)
            if pin is None:
                if user.is_active and user.email_verified and user.deactivated_at is None:
                    no_inventory += 1
                else:
                    skipped += 1
            else:
                allocated += 1
        if allocated:
            self.message_user(
                request,
                f"Назначени ПИН кодове: {allocated}; имейлите се изпращат.",
                messages.SUCCESS,
            )
        if no_inventory:
            self.message_user(
                request,
                f"{no_inventory} потребител(и) не получиха ПИН код: няма свободни "
                "ПИН кодове в наличност.",
                messages.ERROR,
            )
        if skipped:
            self.message_user(
                request,
                f"Пропуснати потребители: {skipped} (вече имат ПИН код или не са допустими).",
                messages.WARNING,
            )

    @admin.action(description="Активирай отново избраните потребители")
    def reactivate_users(self, request, queryset):
        reactivated = 0
        for user in queryset.filter(deactivated_at__isnull=False):
            try:
                services.reactivate_user(user)
                reactivated += 1
            except ValidationError as exc:
                self.message_user(request, f"{user.email}: {exc.messages[0]}", messages.ERROR)
        if reactivated:
            self.message_user(
                request,
                f"Активирани отново: {reactivated}. Отказаните резервации остават "
                "отказани, а старите ПИН кодове не се възстановяват — използвайте "
                "„Назначи ПИН кодове“, за да дадете нов ПИН код.",
                messages.SUCCESS,
            )

    @admin.action(description="Деактивирай избраните потребители")
    def deactivate_users(self, request, queryset):
        deactivated = 0
        for user in queryset:
            try:
                services.deactivate_user(user, actor=request.user)
                deactivated += 1
            except ValidationError as exc:
                self.message_user(request, f"{user.email}: {exc.messages[0]}", messages.ERROR)
        if deactivated:
            self.message_user(request, f"Деактивирани потребители: {deactivated}.", messages.SUCCESS)


class ClaimedFilter(admin.SimpleListFilter):
    title = "регистрация"
    parameter_name = "claimed"

    def lookups(self, request, model_admin):
        return [("yes", "Регистрирани"), ("no", "Нерегистрирани")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(registered_user__isnull=False)
        if self.value() == "no":
            return queryset.filter(registered_user__isnull=True)
        return queryset


class ApprovedResidentForm(forms.ModelForm):
    class Meta:
        model = ApprovedResident
        fields = ["first_name", "last_name", "registered_user", "is_eligible"]

    def clean(self):
        cleaned = super().clean()
        entry = self.instance
        if (
            entry.pk
            and not cleaned.get("is_eligible", True)
            and entry.registered_user is not None
        ):
            # Revoking eligibility deactivates the linked account; that must
            # not be a way around the last-active-administrator safeguard.
            user = entry.registered_user
            services.check_admin_change(
                user, is_active=False, is_staff=user.is_staff, is_superuser=user.is_superuser
            )
        return cleaned


@admin.register(ApprovedResident)
class ApprovedResidentAdmin(admin.ModelAdmin):
    form = ApprovedResidentForm
    list_display = ["first_name", "last_name", "is_eligible", "is_claimed", "registered_user", "created_at"]
    list_filter = ["is_eligible", ClaimedFilter]
    actions = ["mark_ineligible", "mark_eligible"]
    search_fields = ["first_name", "last_name", "normalized_first_name", "normalized_last_name"]
    autocomplete_fields = ["registered_user"]
    change_list_template = "admin/accounts/approvedresident/change_list.html"

    @admin.display(boolean=True, description="Регистриран")
    def is_claimed(self, obj: ApprovedResident) -> bool:
        return obj.is_claimed

    def save_model(self, request, obj, form, change):
        was_eligible = (
            ApprovedResident.objects.get(pk=obj.pk).is_eligible if change else None
        )
        if change and was_eligible and not obj.is_eligible:
            # Route eligibility revocation through the full workflow so the
            # linked account is safely deactivated in the same transaction.
            services.revoke_eligibility(obj, actor=request.user)
            if obj.registered_user is not None:
                self.message_user(
                    request,
                    f"{obj} вече не е допустим: свързаният профил беше "
                    "деактивиран, бъдещите резервации — отказани, а назначеният "
                    "ПИН код е отбелязан за физическо премахване.",
                    messages.WARNING,
                )
            return
        super().save_model(request, obj, form, change)

    @admin.action(description="Отбележи като недопустими (деактивира свързаните профили)")
    def mark_ineligible(self, request, queryset):
        revoked = 0
        for entry in queryset.filter(is_eligible=True):
            try:
                services.revoke_eligibility(entry, actor=request.user)
                revoked += 1
            except ValidationError as exc:
                self.message_user(request, f"{entry}: {exc.messages[0]}", messages.ERROR)
        if revoked:
            self.message_user(
                request,
                f"Отбелязани като недопустими: {revoked}. Свързаните профили бяха "
                "деактивирани; проверете ПИН кодовете, чакащи физическо премахване.",
                messages.SUCCESS,
            )

    @admin.action(description="Върни допустимостта (не активира профилите)")
    def mark_eligible(self, request, queryset):
        restored = 0
        for entry in queryset.filter(is_eligible=False):
            services.restore_eligibility(entry)
            restored += 1
        if restored:
            self.message_user(
                request,
                f"Върната допустимост: {restored}. Свързаните профили остават "
                "деактивирани, докато не бъдат изрично активирани от страницата "
                "Потребители.",
                messages.SUCCESS,
            )

    def has_delete_permission(self, request, obj=None):
        # A claimed entry backs a registered account; unlink or deactivate instead.
        if obj is not None and obj.is_claimed:
            return False
        return super().has_delete_permission(request, obj)

    def get_urls(self):
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="accounts_approvedresident_import_csv",
            )
        ]
        return custom + super().get_urls()

    def import_csv_view(self, request):
        if not self.has_add_permission(request):
            self.message_user(request, "Нямате право да добавяте одобрени живущи.", messages.ERROR)
            return redirect("admin:accounts_approvedresident_changelist")

        form = ApprovedResidentCSVForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            created, skipped, errors = self._import_csv(form.cleaned_data["csv_file"])
            if created:
                self.message_user(request, f"Импортирани одобрени живущи: {created}.", messages.SUCCESS)
            if skipped:
                self.message_user(
                    request, f"Пропуснати дублирани имена: {skipped}.", messages.WARNING
                )
            for error in errors:
                self.message_user(request, error, messages.ERROR)
            return redirect("admin:accounts_approvedresident_changelist")

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Импортиране на одобрени живущи от CSV",
            "opts": self.model._meta,
            "back_url": reverse("admin:accounts_approvedresident_changelist"),
        }
        return render(request, "admin/accounts/approvedresident/csv_import.html", context)

    @staticmethod
    def _import_csv(uploaded_file) -> tuple[int, int, list[str]]:
        created = skipped = 0
        errors: list[str] = []
        try:
            text = uploaded_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            return 0, 0, ["Файлът не е валиден UTF-8 текст."]

        for line_number, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            if line_number == 1 and [cell.strip().lower() for cell in row[:2]] == [
                "first_name",
                "last_name",
            ]:
                continue
            if len(row) < 2 or not row[0].strip() or not row[1].strip():
                errors.append(f"Ред {line_number}: очакват се две колони (име, фамилия).")
                continue
            try:
                with transaction.atomic():
                    ApprovedResident.objects.create(
                        first_name=row[0].strip(), last_name=row[1].strip()
                    )
                created += 1
            except IntegrityError:
                skipped += 1
        return created, skipped, errors
