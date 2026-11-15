import csv
import io

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

    title = "lockbox PIN"
    parameter_name = "pin"

    def lookups(self, request, model_admin):
        return [("awaiting", "Awaiting PIN"), ("assigned", "PIN assigned")]

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
    actions = ["deactivate_users", "allocate_pins"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "email_verified",
                    "deactivated_at",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
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
            elif obj.is_active:
                obj.deactivated_at = None
        super().save_model(request, obj, form, change)
        if deactivating:
            # Keep the change-form path consistent with services.deactivate_user:
            # a deactivated resident's PIN must be flagged for physical removal.
            from access_codes.services import mark_revoke_pending_for_user

            mark_revoke_pending_for_user(obj, actor=request.user)

    def has_delete_permission(self, request, obj=None):
        allowed = super().has_delete_permission(request, obj)
        if allowed and obj is not None:
            try:
                services.check_admin_delete(obj)
            except ValidationError:
                return False
        return allowed

    @admin.display(description="Lockbox PIN")
    def pin_status(self, obj: User) -> str:
        from access_codes.services import user_assigned_pin

        if user_assigned_pin(obj) is not None:
            return "Assigned"
        if obj.is_active and obj.email_verified and obj.deactivated_at is None:
            return "Awaiting"
        return "—"

    @admin.action(description="Allocate lockbox PINs to selected users")
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
                f"Allocated {allocated} PIN(s); delivery emails are being sent.",
                messages.SUCCESS,
            )
        if no_inventory:
            self.message_user(
                request,
                f"{no_inventory} eligible user(s) could not get a PIN: no AVAILABLE "
                "PINs in the inventory.",
                messages.ERROR,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} user(s) skipped (already assigned or not eligible).",
                messages.WARNING,
            )

    @admin.action(description="Deactivate selected users")
    def deactivate_users(self, request, queryset):
        deactivated = 0
        for user in queryset:
            try:
                services.deactivate_user(user, actor=request.user)
                deactivated += 1
            except ValidationError as exc:
                self.message_user(request, f"{user.email}: {exc.messages[0]}", messages.ERROR)
        if deactivated:
            self.message_user(request, f"Deactivated {deactivated} user(s).", messages.SUCCESS)


@admin.register(ApprovedResident)
class ApprovedResidentAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "is_eligible", "is_claimed", "registered_user", "created_at"]
    list_filter = ["is_eligible"]
    search_fields = ["first_name", "last_name", "normalized_first_name", "normalized_last_name"]
    autocomplete_fields = ["registered_user"]
    change_list_template = "admin/accounts/approvedresident/change_list.html"

    @admin.display(boolean=True, description="Claimed")
    def is_claimed(self, obj: ApprovedResident) -> bool:
        return obj.is_claimed

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
            self.message_user(request, "You may not add approved residents.", messages.ERROR)
            return redirect("admin:accounts_approvedresident_changelist")

        form = ApprovedResidentCSVForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            created, skipped, errors = self._import_csv(form.cleaned_data["csv_file"])
            if created:
                self.message_user(request, f"Imported {created} approved resident(s).", messages.SUCCESS)
            if skipped:
                self.message_user(
                    request, f"Skipped {skipped} duplicate name(s).", messages.WARNING
                )
            for error in errors:
                self.message_user(request, error, messages.ERROR)
            return redirect("admin:accounts_approvedresident_changelist")

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Import approved residents from CSV",
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
            return 0, 0, ["The file is not valid UTF-8 text."]

        for line_number, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            if line_number == 1 and [cell.strip().lower() for cell in row[:2]] == [
                "first_name",
                "last_name",
            ]:
                continue
            if len(row) < 2 or not row[0].strip() or not row[1].strip():
                errors.append(f"Line {line_number}: expected two columns (first name, last name).")
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
