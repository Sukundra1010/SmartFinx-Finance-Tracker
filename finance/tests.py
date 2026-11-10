import base64
import json
from io import BytesIO
from tempfile import TemporaryDirectory
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (
    Budget,
    Notification,
    Payment,
    SavingsContribution,
    SavingsGoal,
    Subscription,
    SubscriptionPlan,
    Transaction,
)
from .payments import PaymentGatewayError, _sign_esewa_fields, verify_payment


class FinanceAppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="maya", password="SecurePass123!", first_name="Maya"
        )
        self.other_user = User.objects.create_user(
            username="other", password="SecurePass123!"
        )
        self.client.force_login(self.user)

    def test_health_check_is_public_and_returns_ok(self):
        self.client.logout()

        response = self.client.get(reverse("health_check"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_dashboard_uses_only_current_users_records_and_decimal_totals(self):
        Transaction.objects.create(
            user=self.user, kind="income", title="Salary", amount=Decimal("1000.25"),
            category="Salary", date=date(2026, 11, 1),
        )
        Transaction.objects.create(
            user=self.user, kind="expense", title="Lunch", amount=Decimal("100.10"),
            category="Food", date=date(2026, 11, 2),
        )
        Transaction.objects.create(
            user=self.other_user, kind="income", title="Private", amount=Decimal("9000.00"),
            category="Salary", date=date(2026, 11, 1),
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["balance"], Decimal("900.15"))
        self.assertEqual(response.context["monthly_income"], Decimal("1000.25"))
        self.assertEqual(response.context["monthly_expenses"], Decimal("100.10"))
        self.assertEqual(list(response.context["recent_transactions"].values_list("title", flat=True)), ["Lunch", "Salary"])
        self.assertContains(
            response,
            f'class="stat-budget-action" href="{reverse("budget_create")}"',
        )
        self.assertContains(response, "Create budget")

    def test_registration_creates_account_and_logs_user_in(self):
        self.client.logout()
        response = self.client.post(reverse("register"), {
            "username": "new-user",
            "first_name": "New",
            "last_name": "User",
            "email": "new@example.com",
            "password1": "StrongPass321!Safe",
            "password2": "StrongPass321!Safe",
        })

        self.assertRedirects(response, reverse("dashboard"))
        user = User.objects.get(username="new-user")
        self.assertEqual(user.last_name, "User")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_profile_photo_upload_is_validated_and_displayed(self):
        image_buffer = BytesIO()
        Image.new("RGB", (32, 32), color="teal").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        uploaded_image = SimpleUploadedFile(
            "profile.png", image_buffer.read(), content_type="image/png"
        )

        with TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse("profile"),
                    {
                        "first_name": self.user.first_name,
                        "last_name": "",
                        "email": "maya@example.com",
                        "monthly_income_target": "0.00",
                        "image": uploaded_image,
                    },
                )

                self.assertRedirects(
                    response,
                    reverse("profile"),
                )
                self.user.profile.refresh_from_db()
                self.assertTrue(self.user.profile.image)
                self.assertTrue(self.user.profile.image.storage.exists(self.user.profile.image.name))
                self.assertIn(
                    f"/media/{self.user.profile.image.name}",
                    self.client.get(reverse("profile")).content.decode(),
                )
                self.assertContains(self.client.get(reverse("dashboard")), "avatar-photo")

    def test_profile_photo_rejects_non_image_files(self):
        uploaded_file = SimpleUploadedFile(
            "not-an-image.png", b"not an image", content_type="image/png"
        )

        response = self.client.post(
            reverse("profile"),
            {
                "first_name": self.user.first_name,
                "last_name": "",
                "email": self.user.email,
                "monthly_income_target": "0.00",
                "image": uploaded_file,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid image")
        self.assertFalse(self.user.profile.image)

    def test_transaction_create_filter_edit_and_delete(self):
        create_response = self.client.post(reverse("transaction_create"), {
            "kind": "expense",
            "title": "Market",
            "amount": "42.50",
            "category": "Food",
            "date": "2026-11-05",
            "payment_method": "cash",
            "description": "",
        })
        self.assertRedirects(create_response, reverse("transactions"))
        item = Transaction.objects.get(user=self.user, title="Market")
        self.assertContains(self.client.get(reverse("transactions") + "?kind=expense&category=Food"), "Market")
        self.assertNotContains(self.client.get(reverse("transactions") + "?kind=income"), "Market")

        edit_response = self.client.post(reverse("transaction_edit", args=[item.pk]), {
            "kind": "expense",
            "title": "Groceries",
            "amount": "52.50",
            "category": "Food",
            "date": "2026-11-05",
            "payment_method": "credit_card",
            "description": "",
        })
        self.assertRedirects(edit_response, reverse("transactions"))
        item.refresh_from_db()
        self.assertEqual(item.title, "Groceries")
        self.assertEqual(item.amount, Decimal("52.50"))
        self.assertEqual(item.payment_method, "credit_card")
        self.assertContains(
            self.client.get(reverse("transactions")),
            "Credit Card",
        )

        delete_response = self.client.post(reverse("transaction_delete", args=[item.pk]))
        self.assertRedirects(delete_response, reverse("transactions"))
        self.assertFalse(Transaction.objects.filter(pk=item.pk).exists())

    def test_payment_methods_are_optional_and_validate_available_options(self):
        create_page = self.client.get(reverse("transaction_create"))
        for label in (
            "Select Payment Method",
            "Cash",
            "Bank Transfer",
            "eSewa",
            "Khalti",
            "IME Pay",
            "Debit Card",
            "Credit Card",
            "Online Payment",
            "Other",
        ):
            self.assertContains(create_page, label)
        self.assertNotContains(
            self.client.get(reverse("budget_create")),
            "Select Payment Method",
        )

        base_data = {
            "kind": "expense",
            "amount": "25.00",
            "category": "Food",
            "date": "2026-11-05",
            "description": "",
        }
        for title, payment_method in (
            ("Cash purchase", "cash"),
            ("eSewa purchase", "esewa"),
            ("Unspecified purchase", None),
        ):
            transaction_data = {**base_data, "title": title}
            if payment_method is not None:
                transaction_data["payment_method"] = payment_method
            response = self.client.post(
                reverse("transaction_create"),
                transaction_data,
            )
            self.assertRedirects(response, reverse("transactions"))

        self.assertEqual(
            list(
                Transaction.objects.filter(user=self.user)
                .order_by("title")
                .values_list("payment_method", flat=True)
            ),
            ["cash", "", "esewa"],
        )
        history = self.client.get(reverse("transactions"))
        self.assertContains(history, "Cash")
        self.assertContains(history, "eSewa")
        filtered_history = self.client.get(
            reverse("transactions"),
            {"payment_method": "esewa"},
        )
        self.assertContains(filtered_history, "eSewa purchase")
        self.assertNotContains(filtered_history, "Cash purchase")

        invalid_response = self.client.post(
            reverse("transaction_create"),
            {**base_data, "title": "Invalid method", "payment_method": "wire"},
        )
        self.assertEqual(invalid_response.status_code, 200)
        self.assertFalse(
            Transaction.objects.filter(user=self.user, title="Invalid method").exists()
        )

    def test_legacy_payment_method_remains_selected_when_editing(self):
        transaction = Transaction.objects.create(
            user=self.user,
            kind="expense",
            title="Old wallet transaction",
            amount=Decimal("25.00"),
            category="Food",
            date=date(2025, 6, 1),
            payment_method="wallet",
        )

        form_response = self.client.get(
            reverse("transaction_edit", args=[transaction.pk])
        )
        self.assertContains(form_response, "Digital wallet")
        self.assertContains(form_response, 'value="wallet" selected')

        edit_response = self.client.post(
            reverse("transaction_edit", args=[transaction.pk]),
            {
                "kind": "expense",
                "title": "Updated wallet transaction",
                "amount": "30.00",
                "category": "Food",
                "date": "2025-06-01",
                "payment_method": "wallet",
                "description": "",
            },
        )
        self.assertRedirects(edit_response, reverse("transactions"))
        transaction.refresh_from_db()
        self.assertEqual(transaction.payment_method, "wallet")

    def test_user_cannot_edit_or_delete_another_users_transaction(self):
        private_item = Transaction.objects.create(
            user=self.other_user, kind="expense", title="Private", amount=Decimal("20.00"),
            category="Other", date=date(2026, 11, 1),
        )
        self.assertEqual(
            self.client.get(reverse("transaction_edit", args=[private_item.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("transaction_delete", args=[private_item.pk])).status_code,
            404,
        )
        self.assertTrue(Transaction.objects.filter(pk=private_item.pk).exists())

    def test_budget_spending_and_approaching_limit_notification(self):
        budget = Budget.objects.create(
            user=self.user, category="Food", year=2026, month=11, amount=Decimal("1000.00")
        )
        response = self.client.post(reverse("transaction_create"), {
            "kind": "expense",
            "title": "Dinner",
            "amount": "850.00",
            "category": "Food",
            "date": "2026-11-08",
            "payment_method": "cash",
            "description": "",
        })

        self.assertRedirects(response, reverse("transactions"))
        self.assertEqual(budget.spent, Decimal("850.00"))
        self.assertTrue(Notification.objects.filter(user=self.user, message__contains="80%").exists())

    def test_budget_create_reports_duplicate_category_month_instead_of_server_error(self):
        Budget.objects.create(
            user=self.user,
            category="Food",
            year=2026,
            month=11,
            amount=Decimal("1000.00"),
        )

        response = self.client.post(
            reverse("budget_create"),
            {
                "category": "Food",
                "year": "2026",
                "month": "11",
                "amount": "2000.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "A budget for this category and month already exists."
        )
        self.assertEqual(
            Budget.objects.filter(
                user=self.user, category="Food", year=2026, month=11
            ).count(),
            1,
        )

    def test_created_current_month_budget_appears_in_dashboard_total(self):
        today = timezone.localdate()

        form_page = self.client.get(reverse("budget_create"))
        self.assertContains(form_page, "Create budget")

        response = self.client.post(
            reverse("budget_create"),
            {
                "category": "Food",
                "year": str(today.year),
                "month": str(today.month),
                "amount": "4200.25",
            },
        )

        self.assertRedirects(response, reverse("dashboard"))
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Rs. 4200.25")
        self.assertContains(dashboard, "Monthly budgets")
        self.assertContains(dashboard, "Food")

    def test_created_past_month_budget_returns_to_that_month(self):
        response = self.client.post(
            reverse("budget_create"),
            {
                "category": "Food",
                "year": "2025",
                "month": "10",
                "amount": "800.00",
            },
        )

        self.assertRedirects(response, f"{reverse('budgets')}?year=2025&month=10")

    def test_budget_edit_rejects_an_existing_category_month(self):
        budget = Budget.objects.create(
            user=self.user,
            category="Food",
            year=2026,
            month=11,
            amount=Decimal("1000.00"),
        )
        Budget.objects.create(
            user=self.user,
            category="Travel",
            year=2026,
            month=11,
            amount=Decimal("1500.00"),
        )

        response = self.client.post(
            reverse("budget_edit", args=[budget.pk]),
            {
                "category": "Travel",
                "year": "2026",
                "month": "11",
                "amount": "2000.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "A budget for this category and month already exists."
        )
        budget.refresh_from_db()
        self.assertEqual(budget.category, "Food")

    def test_payment_checkout_is_disabled_by_default(self):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )

        page = self.client.get(reverse("payments"))
        self.assertContains(page, "subscription-check.svg")
        self.assertContains(page, 'class="icon-button payment-nav-link"')
        self.assertContains(page, "Payments")
        self.assertNotContains(page, "Pay with eSewa QR")
        self.assertNotContains(page, "esewa-qr.png")

        response = self.client.post(
            reverse("payment_initiate"),
            {"plan": plan.pk, "provider": Payment.Provider.KHALTI},
        )

        self.assertRedirects(response, reverse("payments"))
        self.assertFalse(Payment.objects.exists())

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_ESEWA_PRODUCT_CODE="EPAYTEST",
        PAYMENT_ESEWA_SECRET_KEY="sandbox-secret",
    )
    def test_esewa_checkout_creates_pending_payment_with_signed_fields(self):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )

        response = self.client.post(
            reverse("payment_initiate"),
            {"plan": plan.pk, "provider": Payment.Provider.ESEWA},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "finance/payment_esewa_checkout.html")
        self.assertEqual(
            response.context["gateway_url"],
            "https://rc-epay.esewa.com.np/api/epay/main/v2/form",
        )
        payment = Payment.objects.get(user=self.user)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, plan.price)
        fields = response.context["fields"]
        self.assertEqual(fields["amount"], "100.00")
        self.assertEqual(fields["total_amount"], "100.00")
        self.assertEqual(fields["transaction_uuid"], str(payment.order_id))
        self.assertEqual(fields["product_code"], "EPAYTEST")
        self.assertEqual(
            fields["signed_field_names"],
            "total_amount,transaction_uuid,product_code",
        )
        self.assertEqual(
            fields["signature"],
            _sign_esewa_fields(
                fields, fields["signed_field_names"].split(",")
            ),
        )
        self.assertContains(response, "Continue to eSewa sandbox")

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_KHALTI_SECRET_KEY="sandbox-test-key",
    )
    @patch("finance.views.initiate_khalti")
    def test_sandbox_checkout_creates_pending_payment(self, initiate_khalti):
        initiate_khalti.return_value = "https://test-pay.khalti.com/?pidx=test-pidx"

        def attach_provider_reference(payment, *args, **kwargs):
            payment.provider_payment_id = "test-pidx"
            payment.save(update_fields=["provider_payment_id", "updated_at"])
            return initiate_khalti.return_value

        initiate_khalti.side_effect = attach_provider_reference
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )

        response = self.client.post(
            reverse("payment_initiate"),
            {"plan": plan.pk, "provider": Payment.Provider.KHALTI},
        )

        self.assertRedirects(
            response,
            "https://test-pay.khalti.com/?pidx=test-pidx",
            fetch_redirect_response=False,
        )
        payment = Payment.objects.get(user=self.user)
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(payment.amount, Decimal("100.00"))
        self.assertEqual(payment.provider_payment_id, "test-pidx")
        self.assertEqual(payment.plan_name, "Starter")
        self.assertEqual(payment.duration_days, 30)

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_KHALTI_SECRET_KEY="sandbox-test-key",
    )
    @patch("finance.payments._lookup_khalti")
    def test_browser_success_claim_cannot_confirm_payment(self, lookup_khalti):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.KHALTI,
            amount=plan.price,
            provider_payment_id="test-pidx",
        )
        lookup_khalti.return_value = {
            "pidx": "test-pidx",
            "total_amount": 10000,
            "status": "Pending",
            "transaction_id": None,
            "purchase_order_id": str(payment.order_id),
        }

        response = self.client.get(
            reverse("payment_return", args=[payment.order_id]),
            {"pidx": "test-pidx", "status": "Completed", "total_amount": "10000"},
        )

        self.assertRedirects(
            response,
            reverse("payment_receipt", args=[payment.order_id]),
        )
        lookup_khalti.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_KHALTI_SECRET_KEY="sandbox-test-key",
    )
    @patch("finance.payments._lookup_khalti")
    def test_verified_payment_activates_subscription_once(self, lookup_khalti):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.KHALTI,
            amount=plan.price,
            provider_payment_id="test-pidx",
        )
        lookup_khalti.return_value = {
            "pidx": "test-pidx",
            "total_amount": 10000,
            "status": "Completed",
            "transaction_id": "test-transaction",
            "purchase_order_id": str(payment.order_id),
            "refunded": False,
        }

        response = self.client.get(
            reverse("payment_return", args=[payment.order_id]),
            {"pidx": "test-pidx", "status": "Completed"},
        )
        self.assertRedirects(
            response,
            reverse("payment_receipt", args=[payment.order_id]),
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.transaction_id, "test-transaction")
        self.assertEqual(Subscription.objects.get(user=self.user).plan, plan)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, message__contains="Payment of NPR 100.00"
            ).count(),
            1,
        )

        self.client.get(reverse("payment_return", args=[payment.order_id]))
        self.assertEqual(lookup_khalti.call_count, 1)
        self.assertEqual(Subscription.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, message__contains="Payment of NPR 100.00"
            ).count(),
            1,
        )

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_ESEWA_PRODUCT_CODE="EPAYTEST",
        PAYMENT_ESEWA_SECRET_KEY="sandbox-secret",
    )
    @patch("finance.payments._esewa_status")
    def test_esewa_success_requires_signed_return_and_server_status(self, esewa_status):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )
        esewa_status.return_value = {
            "transaction_uuid": str(payment.order_id),
            "product_code": "EPAYTEST",
            "total_amount": "100.0",
            "status": "COMPLETE",
            "ref_id": "esewa-reference",
        }
        response_data = {
            "transaction_code": "esewa-reference",
            "status": "COMPLETE",
            "total_amount": "100.0",
            "transaction_uuid": str(payment.order_id),
            "product_code": "EPAYTEST",
            "signed_field_names": (
                "transaction_code,status,total_amount,transaction_uuid,"
                "product_code,signed_field_names"
            ),
        }
        response_data["signature"] = _sign_esewa_fields(
            response_data, response_data["signed_field_names"].split(",")
        )
        encoded_response = base64.b64encode(
            json.dumps(response_data).encode("utf-8")
        ).decode("ascii")

        payment = verify_payment(payment, esewa_response=encoded_response)

        self.assertEqual(payment.status, Payment.Status.SUCCEEDED)
        self.assertEqual(payment.transaction_id, "esewa-reference")
        esewa_status.assert_called_once()
        self.assertTrue(Subscription.objects.filter(user=self.user).exists())

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_ESEWA_PRODUCT_CODE="EPAYTEST",
        PAYMENT_ESEWA_SECRET_KEY="sandbox-secret",
    )
    @patch("finance.payments._esewa_status")
    def test_esewa_rejects_amount_above_the_ordered_value(self, esewa_status):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )
        esewa_status.return_value = {
            "transaction_uuid": str(payment.order_id),
            "product_code": "EPAYTEST",
            "total_amount": "100.001",
            "status": "COMPLETE",
            "ref_id": "unexpected-amount",
        }

        payment = verify_payment(payment)

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_ESEWA_PRODUCT_CODE="EPAYTEST",
        PAYMENT_ESEWA_SECRET_KEY="sandbox-secret",
    )
    @patch("finance.payments._esewa_status")
    def test_forged_esewa_return_does_not_change_pending_payment(self, esewa_status):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )
        forged_data = base64.b64encode(
            json.dumps(
                {
                    "status": "COMPLETE",
                    "total_amount": "100.00",
                    "transaction_uuid": str(payment.order_id),
                    "product_code": "EPAYTEST",
                    "signed_field_names": "status,total_amount,transaction_uuid,product_code",
                    "signature": "forged-signature",
                }
            ).encode("utf-8")
        ).decode("ascii")

        with self.assertRaises(PaymentGatewayError):
            verify_payment(payment, esewa_response=forged_data)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())
        esewa_status.assert_not_called()

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_KHALTI_SECRET_KEY="sandbox-test-key",
    )
    @patch("finance.payments._lookup_khalti")
    def test_duplicate_provider_transaction_id_cannot_activate_another_payment(
        self, lookup_khalti
    ):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        Payment.objects.create(
            user=self.other_user,
            plan=plan,
            provider=Payment.Provider.KHALTI,
            amount=plan.price,
            status=Payment.Status.SUCCEEDED,
            transaction_id="already-used-transaction",
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.KHALTI,
            amount=plan.price,
            provider_payment_id="new-pidx",
        )
        lookup_khalti.return_value = {
            "pidx": "new-pidx",
            "total_amount": 10000,
            "status": "Completed",
            "transaction_id": "already-used-transaction",
            "purchase_order_id": str(payment.order_id),
            "refunded": False,
        }

        verify_payment(payment)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    @override_settings(
        PAYMENTS_MODE="sandbox",
        PAYMENT_KHALTI_SECRET_KEY="sandbox-test-key",
    )
    @patch("finance.payments._lookup_khalti")
    def test_provider_confirmed_failure_creates_user_notification(self, lookup_khalti):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.KHALTI,
            amount=plan.price,
            provider_payment_id="canceled-pidx",
        )
        lookup_khalti.return_value = {
            "pidx": "canceled-pidx",
            "total_amount": 10000,
            "status": "User canceled",
            "transaction_id": None,
            "purchase_order_id": str(payment.order_id),
        }

        payment = verify_payment(payment)

        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                message__contains="was not completed",
            ).exists()
        )

    def test_payment_history_receipt_and_report_are_user_scoped(self):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )
        private_payment = Payment.objects.create(
            user=self.other_user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )

        history = self.client.get(reverse("payments"))
        self.assertContains(history, str(payment.order_id))
        self.assertNotContains(history, str(private_payment.order_id))
        self.assertEqual(
            self.client.get(
                reverse("payment_receipt", args=[private_payment.order_id])
            ).status_code,
            404,
        )
        report = self.client.get(reverse("payment_report"))
        self.assertContains(report, str(payment.order_id))
        self.assertNotContains(report, str(private_payment.order_id))

    def test_payment_pages_require_authentication(self):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        payment = Payment.objects.create(
            user=self.user,
            plan=plan,
            provider=Payment.Provider.ESEWA,
            amount=plan.price,
        )
        self.client.logout()

        for url in (
            reverse("payments"),
            reverse("payment_report"),
            reverse("payment_receipt", args=[payment.order_id]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])

    def test_payment_history_pagination_keeps_active_filters(self):
        plan = SubscriptionPlan.objects.create(
            name="Starter", price=Decimal("100.00"), duration_days=30
        )
        for _ in range(16):
            Payment.objects.create(
                user=self.user,
                plan=plan,
                provider=Payment.Provider.ESEWA,
                amount=plan.price,
            )

        first_page = self.client.get(
            reverse("payments"),
            {"status": Payment.Status.PENDING, "provider": Payment.Provider.ESEWA},
        )
        self.assertEqual(
            first_page.context["next_page_url"],
            "?status=pending&provider=esewa&page=2",
        )

        second_page = self.client.get(
            reverse("payments"),
            {
                "status": Payment.Status.PENDING,
                "provider": Payment.Provider.ESEWA,
                "page": 2,
            },
        )
        self.assertEqual(
            second_page.context["previous_page_url"],
            "?status=pending&provider=esewa&page=1",
        )

    def test_savings_contribution_completes_goal_and_is_private(self):
        goal = SavingsGoal.objects.create(
            user=self.user, name="Emergency fund", target_amount=Decimal("500.00")
        )
        response = self.client.post(reverse("contribution_create", args=[goal.pk]), {
            "amount": "500.00",
            "date": "2026-11-09",
            "note": "Monthly saving",
        })
        self.assertRedirects(response, reverse("savings"))
        goal.refresh_from_db()
        self.assertTrue(goal.completed)
        self.assertEqual(goal.saved_amount, Decimal("500.00"))
        self.assertTrue(Notification.objects.filter(user=self.user, message__contains=goal.name).exists())

        other_goal = SavingsGoal.objects.create(
            user=self.other_user, name="Private goal", target_amount=Decimal("100.00")
        )
        contribution = SavingsContribution.objects.create(
            goal=other_goal, amount=Decimal("10.00"), date=date(2026, 11, 9)
        )
        self.assertEqual(
            self.client.get(reverse("contribution_edit", args=[contribution.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("contribution_delete", args=[contribution.pk])).status_code,
            404,
        )

    def test_removing_contribution_reopens_completed_goal(self):
        goal = SavingsGoal.objects.create(
            user=self.user, name="Trip", target_amount=Decimal("100.00"), completed=True
        )
        contribution = SavingsContribution.objects.create(
            goal=goal, amount=Decimal("100.00"), date=date(2026, 11, 9)
        )

        response = self.client.post(reverse("contribution_delete", args=[contribution.pk]))

        self.assertRedirects(response, reverse("savings"))
        goal.refresh_from_db()
        self.assertFalse(goal.completed)

    def test_daily_report_preset_uses_only_today(self):
        today = timezone.localdate()
        Transaction.objects.create(
            user=self.user, kind="income", title="Today", amount=Decimal("12.00"),
            category="Other", date=today,
        )
        Transaction.objects.create(
            user=self.user, kind="income", title="Yesterday", amount=Decimal("8.00"),
            category="Other", date=today - timedelta(days=1),
        )

        response = self.client.get(reverse("reports"), {"period": "daily"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["transactions"].values_list("title", flat=True)),
            ["Today"],
        )

    def test_analytics_yearly_profit_loss_and_history_use_only_current_users_records(self):
        Transaction.objects.create(
            user=self.user, kind="income", title="2025 salary",
            amount=Decimal("1000.00"), category="Salary", date=date(2025, 1, 10),
        )
        Transaction.objects.create(
            user=self.user, kind="expense", title="2025 rent",
            amount=Decimal("300.00"), category="Housing", date=date(2025, 2, 10),
        )
        Transaction.objects.create(
            user=self.user, kind="expense", title="2025 food",
            amount=Decimal("50.00"), category="Food", date=date(2025, 3, 10),
        )
        Transaction.objects.create(
            user=self.user, kind="income", title="2024 salary",
            amount=Decimal("200.00"), category="Salary", date=date(2024, 1, 10),
        )
        Transaction.objects.create(
            user=self.user, kind="expense", title="2024 bills",
            amount=Decimal("260.00"), category="Utilities", date=date(2024, 2, 10),
        )
        Transaction.objects.create(
            user=self.other_user, kind="income", title="Private salary",
            amount=Decimal("9000.00"), category="Salary", date=date(2025, 1, 10),
        )

        response = self.client.get(reverse("analytics"), {"year": "2025"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["year"], 2025)
        self.assertEqual(response.context["year_income"], Decimal("1000.00"))
        self.assertEqual(response.context["year_expenses"], Decimal("350.00"))
        self.assertEqual(response.context["year_net"], Decimal("650.00"))
        self.assertEqual(response.context["year_transaction_count"], 3)
        self.assertEqual(response.context["all_time_income"], Decimal("1200.00"))
        self.assertEqual(response.context["all_time_expenses"], Decimal("610.00"))
        self.assertEqual(response.context["all_time_net"], Decimal("590.00"))
        self.assertEqual(response.context["all_time_transaction_count"], 5)
        self.assertEqual(
            list(response.context["history_transactions"].values_list("title", flat=True)),
            ["2025 food", "2025 rent", "2025 salary"],
        )
        self.assertNotContains(response, "Private salary")

        prior_year = self.client.get(reverse("analytics"), {"year": "2024"})
        self.assertEqual(prior_year.context["year_income"], Decimal("200.00"))
        self.assertEqual(prior_year.context["year_expenses"], Decimal("260.00"))
        self.assertEqual(prior_year.context["year_net"], Decimal("-60.00"))
        self.assertContains(prior_year, "Net loss")
        self.assertContains(prior_year, "−Rs. 60.00")
        self.assertEqual(
            list(prior_year.context["history_transactions"].values_list("title", flat=True)),
            ["2024 bills", "2024 salary"],
        )

    def test_payment_analytics_use_expenses_for_selected_year_and_current_user(self):
        transactions = [
            (self.user, "2025 cash", "cash", "25.00", date(2025, 1, 10)),
            (self.user, "2025 eSewa", "esewa", "40.00", date(2025, 2, 10)),
            (self.user, "2025 debit", "debit_card", "30.00", date(2025, 3, 10)),
            (self.user, "2025 credit", "credit_card", "20.00", date(2025, 4, 10)),
            (self.user, "2024 cash", "cash", "100.00", date(2024, 1, 10)),
            (self.other_user, "Private eSewa", "esewa", "900.00", date(2025, 1, 10)),
        ]
        for user, title, payment_method, amount, transaction_date in transactions:
            Transaction.objects.create(
                user=user,
                kind="expense",
                title=title,
                amount=Decimal(amount),
                category="Other",
                date=transaction_date,
                payment_method=payment_method,
            )

        response = self.client.get(reverse("analytics"), {"year": "2025"})

        self.assertEqual(
            response.context["payment_method_summary"],
            [
                {"label": "Cash", "total": Decimal("25.00")},
                {"label": "eSewa", "total": Decimal("40.00")},
                {"label": "Khalti", "total": Decimal("0.00")},
                {"label": "Bank Transfer", "total": Decimal("0.00")},
                {"label": "Card", "total": Decimal("50.00")},
            ],
        )
        self.assertEqual(
            response.context["payment_method_labels_json"],
            '["Cash", "eSewa", "Debit Card", "Credit Card"]',
        )
        self.assertNotContains(response, "Private eSewa")
        self.assertEqual(
            self.client.get(reverse("analytics"), {"year": "2024"}).context[
                "payment_method_summary"
            ][0]["total"],
            Decimal("100.00"),
        )

    def test_analytics_history_is_paginated_and_keeps_selected_year(self):
        for day in range(1, 17):
            Transaction.objects.create(
                user=self.user,
                kind="expense",
                title=f"Expense {day}",
                amount=Decimal("1.00"),
                category="Food",
                date=date(2025, 1, day),
            )

        response = self.client.get(reverse("analytics"), {"year": "2025"})

        self.assertEqual(response.context["history_page"].paginator.count, 16)
        self.assertEqual(len(response.context["history_transactions"]), 15)
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "?year=2025&amp;history_page=2")

    def test_csv_and_pdf_reports_export_only_the_current_users_records(self):
        Transaction.objects.create(
            user=self.user, kind="income", title="Pay", amount=Decimal("1234.50"),
            category="Salary", date=date(2026, 11, 1),
        )
        Transaction.objects.create(
            user=self.other_user, kind="income", title="Not mine", amount=Decimal("999.00"),
            category="Salary", date=date(2026, 11, 1),
        )
        query = "?start=2026-11-01&end=2026-11-30"

        csv_response = self.client.get(reverse("report_csv") + query)
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("Pay", csv_response.content.decode("utf-8-sig"))
        self.assertNotIn("Not mine", csv_response.content.decode("utf-8-sig"))

        pdf_response = self.client.get(reverse("report_pdf") + query)
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        self.assertNotIn(b"Not mine", pdf_response.content)

    def test_all_authenticated_pages_render(self):
        for name in (
            "dashboard",
            "payments",
            "transactions",
            "budgets",
            "savings",
            "analytics",
            "assistant",
            "reports",
            "payment_report",
            "profile",
            "notifications",
            "transaction_create",
            "budget_create",
            "savings_create",
            "password_change",
        ):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_smart_assistant_responds_using_private_monthly_data(self):
        Transaction.objects.create(
            user=self.user, kind="expense", title="Groceries",
            amount=Decimal("75.00"), category="Food", date=date(2026, 11, 3),
        )
        Transaction.objects.create(
            user=self.other_user, kind="expense", title="Private shopping",
            amount=Decimal("950.00"), category="Shopping", date=date(2026, 11, 3),
        )

        response = self.client.post(
            reverse("assistant"), {"question": "Where did I spend the most?"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Food is your largest expense category this month")
        self.assertNotContains(response, "Private shopping")

    def test_homepage_is_first_for_everyone_and_dashboard_requires_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Money.")
        self.assertContains(response, "Build Your Future.")
        self.assertContains(response, reverse("register"))
        self.assertContains(response, reverse("login"))
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'id="pwa-install"')
        self.assertNotContains(response, "Developed with passion by")
        self.assertContains(response, "© 2026 SmartFinX. All rights reserved.")

        self.client.force_login(self.user)
        signed_in_home = self.client.get(reverse("home"))
        self.assertContains(signed_in_home, "Manage Money.")
        self.assertContains(signed_in_home, reverse("dashboard"))

        logout_response = self.client.post(reverse("logout"))
        self.assertRedirects(logout_response, reverse("home"))

        self.client.logout()
        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertIn(reverse("login"), dashboard_response["Location"])

        private_response = self.client.get(reverse("transactions"))
        self.assertEqual(private_response.status_code, 302)
        self.assertIn(reverse("login"), private_response["Location"])

    def test_pwa_service_worker_is_root_scoped_and_financial_pages_are_not_cached(self):
        response = self.client.get(reverse("service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertEqual(response["Cache-Control"], "no-cache")
        worker_source = b"".join(response.streaming_content).decode()
        self.assertIn('event.request.mode !== "navigate"', worker_source)
        self.assertIn("fetch(event.request)", worker_source)
        self.assertNotIn("cache.put", worker_source)

    def test_dashboard_uses_smartfinx_branding(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "<title>Overview · smartFinX</title>")
        self.assertContains(response, 'alt="SmartFinX"')
        self.assertContains(response, "images/smartfinx-logo.png")
        self.assertContains(response, "images/smartfinx-logo-dark.png")
        self.assertContains(response, "images/smartfinx-icon.png")

    def test_notification_icon_is_used_in_navigation_and_empty_state(self):
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "side-notification-icon")
        self.assertContains(dashboard, "side-settings-icon")
        self.assertContains(dashboard, "mobile-settings-icon")
        self.assertContains(dashboard, 'class="settings-icon"')
        self.assertContains(dashboard, 'aria-label="Notifications"')
        self.assertContains(dashboard, 'class="notification-icon"')
        self.assertContains(dashboard, "images/theme-mode.png")
        self.assertContains(dashboard, 'id="theme-toggle"')
        self.assertContains(dashboard, 'aria-pressed="false"')

        notifications = self.client.get(reverse("notifications"))
        self.assertContains(notifications, "notification-empty-icon")
        self.assertContains(notifications, "You're all caught up")
