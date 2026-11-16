from django.contrib import admin, messages
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import redirect, render
from django.urls import path, reverse

from . import services
from .forms import LockboxPINAddForm, PINImportForm
from .models import LockboxPIN, PINAuditLog, PINDelivery


@admin.register(LockboxPIN)
class LockboxPINAdmin(admin.ModelAdmin):
    """Inventory management without ever displaying PIN values.

    Direct field editing is disabled; every status change goes through an
    explicit, confirmed action so the software state always reflects a
    deliberate statement about the physical lockbox.
    """

    form = LockboxPINAddForm
    list_display = [
        "pk",
        "status",
        "assigned_user",
        "assigned_at",
        "revoke_requested_at",
        "revoke_confirmed_at",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["assigned_user__email", "assigned_user__first_name", "assigned_user__last_name"]
    actions = ["confirm_programmed_action", "confirm_removal_action"]
    change_list_template = "admin/access_codes/lockboxpin/change_list.html"

    def has_change_permission(self, request, obj=None):
        # View-only detail pages; transitions happen via confirmed actions.
        return False

    def has_delete_permission(self, request, obj=None):
        # Inventory is history; retired codes stay as REVOKED records.
        return False

    def save_model(self, request, obj, form, change):
        pin = services.add_pin(form.cleaned_data["pin_value"], actor=request.user)
        # Make the admin flow continue with the created object.
        obj.pk = pin.pk

    def response_add(self, request, obj, post_url_continue=None):
        self.message_user(
            request,
            "ПИН кодът е записан криптиран като „Непрограмиран“. Програмирайте го "
            "в кутията с ключ и използвайте действието „Потвърди програмирането“, "
            "за да стане свободен за назначаване.",
            messages.SUCCESS,
        )
        return redirect("admin:access_codes_lockboxpin_changelist")

    def changelist_view(self, request, extra_context=None):
        counts = services.inventory_counts()
        extra_context = {
            **(extra_context or {}),
            "status_counts": [
                (LockboxPIN.Status(value).label, counts[value])
                for value in LockboxPIN.Status.values
            ],
            "awaiting_pin_count": services.users_awaiting_pin().count(),
        }
        return super().changelist_view(request, extra_context=extra_context)

    # --- Confirmed transition actions ---------------------------------

    def _transition_action(self, request, queryset, *, kind: str):
        config = {
            "programmed": {
                "expected": LockboxPIN.Status.UNPROGRAMMED,
                "title": "Потвърдете, че тези ПИН кодове са програмирани в кутията",
                "warning": (
                    "Потвърждавайте само ПИН кодове, които лично сте програмирали "
                    "в физическата кутия с ключ и сте тествали. Потвърдените "
                    "кодове стават „Свободни“ и могат веднага да бъдат назначени "
                    "на живущи."
                ),
                "service": services.confirm_programmed,
                "done": "отбелязани като „Свободни“",
            },
            "removal": {
                "expected": LockboxPIN.Status.REVOKE_PENDING,
                "title": "Потвърдете, че тези ПИН кодове са физически премахнати от кутията",
                "warning": (
                    "Потвърждавайте само ПИН кодове, които лично сте изтрили от "
                    "физическата кутия с ключ. До премахването кодът продължава "
                    "да отваря кутията, независимо какво показва приложението."
                ),
                "service": services.confirm_physical_removal,
                "done": "отбелязани като „Премахнати“",
            },
        }[kind]

        eligible = queryset.filter(status=config["expected"])
        skipped = queryset.exclude(status=config["expected"]).count()

        if request.POST.get("transition_confirmed"):
            count = config["service"](eligible, actor=request.user)
            if count:
                self.message_user(request, f"ПИН кодове {config['done']}: {count}.", messages.SUCCESS)
            if skipped:
                self.message_user(
                    request,
                    f"Пропуснати ПИН кодове: {skipped} (не са в необходимото "
                    f"състояние {config['expected']}).",
                    messages.WARNING,
                )
            return None

        return render(
            request,
            "admin/access_codes/lockboxpin/confirm_transition.html",
            {
                **self.admin_site.each_context(request),
                "title": config["title"],
                "warning": config["warning"],
                "eligible": eligible,
                "skipped": skipped,
                "action_name": f"confirm_{kind}_action",
                "queryset_ids": [str(pk) for pk in queryset.values_list("pk", flat=True)],
                "opts": self.model._meta,
            },
        )

    @admin.action(description="Потвърди програмирането в кутията (Непрограмиран → Свободен)")
    def confirm_programmed_action(self, request, queryset):
        return self._transition_action(request, queryset, kind="programmed")

    @admin.action(description="Потвърди физическото премахване (Чака премахване → Премахнат)")
    def confirm_removal_action(self, request, queryset):
        return self._transition_action(request, queryset, kind="removal")

    # --- CSV import ----------------------------------------------------

    def get_urls(self):
        custom = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="access_codes_lockboxpin_import_csv",
            )
        ]
        return custom + super().get_urls()

    def import_csv_view(self, request):
        if not self.has_add_permission(request):
            self.message_user(request, "Нямате право да добавяте ПИН кодове.", messages.ERROR)
            return redirect("admin:access_codes_lockboxpin_changelist")

        form = PINImportForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                result = services.import_pins(form.cleaned_data["lines"], actor=request.user)
            except ImproperlyConfigured as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return redirect("admin:access_codes_lockboxpin_changelist")
            if result.created:
                self.message_user(
                    request,
                    f"Импортирани ПИН кодове: {result.created} (като „Непрограмирани“). "
                    "Програмирайте ги в кутията с ключ и потвърдете, за да станат свободни.",
                    messages.SUCCESS,
                )
            if result.duplicates:
                self.message_user(
                    request, f"Пропуснати дублирани ПИН кодове: {result.duplicates}.", messages.WARNING
                )
            for error in result.errors:
                self.message_user(request, error, messages.ERROR)
            return redirect("admin:access_codes_lockboxpin_changelist")

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Импортиране на ПИН кодове от CSV",
            "opts": self.model._meta,
            "back_url": reverse("admin:access_codes_lockboxpin_changelist"),
        }
        return render(request, "admin/access_codes/lockboxpin/csv_import.html", context)


@admin.register(PINDelivery)
class PINDeliveryAdmin(admin.ModelAdmin):
    list_display = [
        "pk",
        "pin",
        "user",
        "status",
        "attempts",
        "last_attempt_at",
        "sent_at",
        "last_error",
    ]
    list_filter = ["status"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    actions = ["retry_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Опитай отново неуспешните/чакащите изпращания")
    def retry_action(self, request, queryset):
        sent, failed = services.retry_deliveries(queryset, actor=request.user)
        if sent:
            self.message_user(request, f"Изпратени имейли с ПИН код: {sent}.", messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                f"Неуспешни опити за изпращане: {failed}; вижте записите за "
                "изпращане за подробности.",
                messages.WARNING,
            )
        if not sent and not failed:
            self.message_user(
                request, "Няма избрани неуспешни или чакащи изпращания.", messages.INFO
            )


@admin.register(PINAuditLog)
class PINAuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "action", "pin", "actor", "target_user", "detail"]
    list_filter = ["action"]
    search_fields = [
        "actor__email",
        "target_user__email",
        "target_user__first_name",
        "target_user__last_name",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
