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
            "PIN stored encrypted as UNPROGRAMMED. Program it into the lockbox, "
            "then use the 'Confirm programmed' action to make it available.",
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
                "title": "Confirm these PINs are programmed into the lockbox",
                "warning": (
                    "Only confirm PINs you have personally programmed into the "
                    "physical lockbox and tested. Confirmed PINs become "
                    "AVAILABLE and may be assigned to residents immediately."
                ),
                "service": services.confirm_programmed,
                "done": "marked AVAILABLE",
            },
            "removal": {
                "expected": LockboxPIN.Status.REVOKE_PENDING,
                "title": "Confirm these PINs are physically removed from the lockbox",
                "warning": (
                    "Only confirm PINs you have personally deleted from the "
                    "physical lockbox. Until removal, the code still opens the "
                    "lockbox regardless of what this application shows."
                ),
                "service": services.confirm_physical_removal,
                "done": "marked REVOKED",
            },
        }[kind]

        eligible = queryset.filter(status=config["expected"])
        skipped = queryset.exclude(status=config["expected"]).count()

        if request.POST.get("transition_confirmed"):
            count = config["service"](eligible, actor=request.user)
            if count:
                self.message_user(request, f"{count} PIN(s) {config['done']}.", messages.SUCCESS)
            if skipped:
                self.message_user(
                    request,
                    f"{skipped} PIN(s) skipped (not in the required "
                    f"{config['expected']} state).",
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

    @admin.action(description="Confirm programmed into lockbox (UNPROGRAMMED → AVAILABLE)")
    def confirm_programmed_action(self, request, queryset):
        return self._transition_action(request, queryset, kind="programmed")

    @admin.action(description="Confirm physical removal (REVOKE_PENDING → REVOKED)")
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
            self.message_user(request, "You may not add PINs.", messages.ERROR)
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
                    f"Imported {result.created} PIN(s) as UNPROGRAMMED. Program them "
                    "into the lockbox, then confirm them to make them available.",
                    messages.SUCCESS,
                )
            if result.duplicates:
                self.message_user(
                    request, f"Skipped {result.duplicates} duplicate PIN(s).", messages.WARNING
                )
            for error in result.errors:
                self.message_user(request, error, messages.ERROR)
            return redirect("admin:access_codes_lockboxpin_changelist")

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Import lockbox PINs from CSV",
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

    @admin.action(description="Retry failed/pending deliveries")
    def retry_action(self, request, queryset):
        sent, failed = services.retry_deliveries(queryset, actor=request.user)
        if sent:
            self.message_user(request, f"Sent {sent} PIN email(s).", messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                f"{failed} delivery attempt(s) did not succeed; see the delivery "
                "records for details.",
                messages.WARNING,
            )
        if not sent and not failed:
            self.message_user(
                request, "No failed or pending deliveries were selected.", messages.INFO
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
