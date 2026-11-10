from django.contrib import admin

from .models import (
    Budget,
    Notification,
    Payment,
    Profile,
    SavingsContribution,
    SavingsGoal,
    Subscription,
    SubscriptionPlan,
    Transaction,
)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "kind",
        "category",
        "payment_method",
        "amount",
        "date",
    )
    list_filter = (
        "kind",
        "category",
        "payment_method",
        "date",
    )
    search_fields = ("title", "user__username")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "category",
        "year",
        "month",
        "amount",
    )
    list_filter = (
        "year",
        "month",
        "category",
    )


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "duration_days", "is_active", "created_at")
    list_filter = ("is_active", "duration_days")
    search_fields = ("name", "description")
    list_editable = ("price", "duration_days", "is_active")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order_id",
        "user",
        "plan",
        "provider",
        "status",
        "amount",
        "transaction_id",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "provider", "created_at", "paid_at")
    search_fields = (
        "order_id",
        "transaction_id",
        "provider_payment_id",
        "user__username",
        "user__email",
        "plan__name",
    )
    readonly_fields = (
        "user",
        "plan",
        "provider",
        "amount",
        "plan_name",
        "duration_days",
        "order_id",
        "provider_payment_id",
        "transaction_id",
        "status",
        "status_message",
        "created_at",
        "updated_at",
        "paid_at",
    )
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "starts_at", "expires_at", "updated_at")
    list_filter = ("plan", "expires_at")
    search_fields = ("user__username", "user__email", "plan__name")


admin.site.register(Profile)
admin.site.register(SavingsGoal)
admin.site.register(SavingsContribution)
admin.site.register(Notification)
