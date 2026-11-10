from decimal import Decimal
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from .constants import CATEGORY_CHOICES


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    image = models.ImageField(upload_to="profile_images/", blank=True)
    currency = models.CharField(max_length=8, default="NPR")
    monthly_income_target = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s profile"


class Transaction(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        ESEWA = "esewa", "eSewa"
        KHALTI = "khalti", "Khalti"
        IME_PAY = "ime_pay", "IME Pay"
        DEBIT_CARD = "debit_card", "Debit Card"
        CREDIT_CARD = "credit_card", "Credit Card"
        ONLINE_PAYMENT = "online_payment", "Online Payment"
        OTHER = "other", "Other"
        LEGACY_CARD = "card", "Card"
        LEGACY_BANK = "bank", "Bank transfer"
        LEGACY_WALLET = "wallet", "Digital wallet"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions"
    )
    kind = models.CharField(max_length=8, choices=Kind.choices)
    title = models.CharField(max_length=120)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES, default="Other")
    date = models.DateField()
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, blank=True, default=""
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["user", "kind", "date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="transaction_amount_positive",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title} ({self.amount})"


class Budget(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets"
    )
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month", "category"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category", "year", "month"],
                name="unique_user_category_month_budget",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="budget_amount_positive",
            ),
        ]
        indexes = [models.Index(fields=["user", "year", "month"])]

    @property
    def spent(self):
        result = Transaction.objects.filter(
            user=self.user,
            kind=Transaction.Kind.EXPENSE,
            category=self.category,
            date__year=self.year,
            date__month=self.month,
        ).aggregate(total=Sum("amount"))
        return result["total"] or Decimal("0.00")

    @property
    def remaining(self):
        return self.amount - self.spent

    @property
    def utilization(self):
        if not self.amount:
            return 0
        return round(float(self.spent / self.amount * 100), 1)

    def __str__(self):
        return f"{self.category} budget ({self.year}-{self.month:02})"


class SavingsGoal(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings_goals"
    )
    name = models.CharField(max_length=120)
    target_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    target_date = models.DateField(null=True, blank=True)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(target_amount__gt=0), name="savings_target_positive"
            )
        ]

    @property
    def saved_amount(self):
        result = self.contributions.aggregate(total=Sum("amount"))
        return result["total"] or Decimal("0.00")

    @property
    def remaining_amount(self):
        return max(self.target_amount - self.saved_amount, Decimal("0.00"))

    @property
    def progress(self):
        if not self.target_amount:
            return 0
        return min(round(float(self.saved_amount / self.target_amount * 100), 1), 100)

    def __str__(self):
        return self.name


class SavingsContribution(models.Model):
    goal = models.ForeignKey(
        SavingsGoal, on_delete=models.CASCADE, related_name="contributions"
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="savings_contribution_positive"
            )
        ]

    def __str__(self):
        return f"{self.goal.name}: {self.amount}"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=240, blank=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    duration_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1)]
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gt=0), name="subscription_plan_price_positive"
            ),
            models.CheckConstraint(
                condition=Q(duration_days__gt=0),
                name="subscription_plan_duration_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.price} NPR / {self.duration_days} days)"


class Payment(models.Model):
    class Provider(models.TextChoices):
        ESEWA = "esewa", "eSewa"
        KHALTI = "khalti", "Khalti"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Successful"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="payments"
    )
    provider = models.CharField(max_length=12, choices=Provider.choices)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    plan_name = models.CharField(max_length=80, default="")
    duration_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1)]
    )
    order_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider_payment_id = models.CharField(
        max_length=120, unique=True, null=True, blank=True
    )
    transaction_id = models.CharField(max_length=120, unique=True, null=True, blank=True)
    status_message = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="payment_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(duration_days__gt=0),
                name="payment_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status", "created_at"]),
            models.Index(fields=["provider", "status"]),
        ]

    def __str__(self):
        return f"{self.order_id} ({self.get_status_display()})"


class Subscription(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions"
    )
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expires_at"]

    @property
    def is_active(self):
        return self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.user} — {self.plan}"
