from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import (
    Budget,
    Payment,
    Profile,
    SavingsContribution,
    SavingsGoal,
    SubscriptionPlan,
    Transaction,
)


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")


class ProfileForm(forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()

    class Meta:
        model = Profile
        fields = ("image", "monthly_income_target")
        widgets = {
            "image": forms.FileInput(
                attrs={"accept": "image/jpeg,image/png,image/webp"}
            )
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        self.fields["first_name"].initial = self.user.first_name
        self.fields["last_name"].initial = self.user.last_name
        self.fields["email"].initial = self.user.email

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image:
            extension = image.name.rsplit(".", 1)[-1].lower()
            if extension not in {"jpg", "jpeg", "png", "webp"}:
                raise forms.ValidationError(
                    "Choose a JPG, PNG, or WebP image."
                )
            if image.size > 5 * 1024 * 1024:
                raise forms.ValidationError(
                    "Profile images must be 5 MB or smaller."
                )
        return image

    def save(self, commit=True):
        profile = super().save(commit=False)

        profile.user.first_name = self.cleaned_data["first_name"]
        profile.user.last_name = self.cleaned_data["last_name"]
        profile.user.email = self.cleaned_data["email"]

        if commit:
            profile.user.save()
            profile.save()

        return profile


class TransactionForm(forms.ModelForm):
    kind = forms.ChoiceField(
        choices=Transaction.Kind.choices,
        initial=Transaction.Kind.EXPENSE,
        widget=forms.Select,
    )

    class Meta:
        model = Transaction
        fields = (
            "kind",
            "amount",
            "category",
            "date",
            "payment_method",
            "title",
            "description",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. Groceries"}),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_payment_methods = [
            choice
            for choice in Transaction.PaymentMethod.choices
            if choice[0] not in {"card", "bank", "wallet"}
        ]
        payment_method_field = self.fields["payment_method"]
        payment_method_field.required = False
        payment_method_field.choices = [
            ("", "Select Payment Method"),
            *current_payment_methods,
        ]
        if self.instance.pk and self.instance.payment_method in {
            "card",
            "bank",
            "wallet",
        }:
            payment_method_field.choices = [
                *payment_method_field.choices,
                (
                    self.instance.payment_method,
                    self.instance.get_payment_method_display(),
                ),
            ]
        if not self.instance.pk:
            self.fields["kind"].initial = Transaction.Kind.EXPENSE
            self.fields["category"].initial = "Food"

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")

        return amount


class BudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ("category", "year", "month", "amount")

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        year = cleaned_data.get("year")
        month = cleaned_data.get("month")

        if category and year and month:
            budgets = Budget.objects.filter(
                user=self.user, category=category, year=year, month=month
            )
            if self.instance.pk:
                budgets = budgets.exclude(pk=self.instance.pk)
            if budgets.exists():
                self.add_error(
                    "category",
                    "A budget for this category and month already exists.",
                )

        return cleaned_data

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount <= 0:
            raise forms.ValidationError("Budget must be greater than zero.")

        return amount


class PaymentInitiationForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=SubscriptionPlan.objects.none())
    provider = forms.ChoiceField(choices=Payment.Provider.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True)


class SavingsGoalForm(forms.ModelForm):
    class Meta:
        model = SavingsGoal
        fields = ("name", "target_amount", "target_date")
        widgets = {"target_date": forms.DateInput(attrs={"type": "date"})}

    def clean_target_amount(self):
        amount = self.cleaned_data["target_amount"]

        if amount <= 0:
            raise forms.ValidationError("Target amount must be greater than zero.")

        return amount


class SavingsContributionForm(forms.ModelForm):
    class Meta:
        model = SavingsContribution
        fields = ("amount", "date", "note")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount <= 0:
            raise forms.ValidationError("Contribution must be greater than zero.")

        return amount
