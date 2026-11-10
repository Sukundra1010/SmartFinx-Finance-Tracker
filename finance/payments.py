import base64
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Notification, Payment, Subscription


logger = logging.getLogger(__name__)

ESEWA_CHECKOUT_URL = "https://rc-epay.esewa.com.np/api/epay/main/v2/form"
ESEWA_STATUS_URL = "https://rc.esewa.com.np/api/epay/transaction/status/"
KHALTI_API_URL = "https://dev.khalti.com/api/v2/epayment"


class PaymentGatewayError(Exception):
    pass


def gateway_is_configured(provider):
    if settings.PAYMENTS_MODE != "sandbox":
        return False
    if provider == Payment.Provider.ESEWA:
        return bool(
            settings.PAYMENT_ESEWA_PRODUCT_CODE
            and settings.PAYMENT_ESEWA_SECRET_KEY
        )
    if provider == Payment.Provider.KHALTI:
        return bool(settings.PAYMENT_KHALTI_SECRET_KEY)
    return False


def _request_json(url, payload, *, secret=None):
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"key {secret}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        logger.warning("Payment gateway returned HTTP %s.", exc.code)
        raise PaymentGatewayError(
            "The payment provider could not process the request."
        ) from exc
    except (URLError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        logger.warning("Payment provider request failed: %s", reason)
        raise PaymentGatewayError(
            "Could not reach the payment provider. Try again shortly."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Payment provider returned an invalid response.")
        raise PaymentGatewayError(
            "The payment provider returned an invalid response."
        ) from exc
    if not isinstance(data, dict):
        raise PaymentGatewayError("The payment provider returned an invalid response.")
    return data


def _sign_esewa_fields(fields, names):
    message = ",".join(f"{name}={fields[name]}" for name in names)
    digest = hmac.new(
        settings.PAYMENT_ESEWA_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _parse_amount(value):
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value).replace(",", ""))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return amount if amount.is_finite() else None


def esewa_checkout_fields(payment, success_url, failure_url):
    amount = f"{payment.amount:.2f}"
    fields = {
        "amount": amount,
        "tax_amount": "0",
        "total_amount": amount,
        "transaction_uuid": str(payment.order_id),
        "product_code": settings.PAYMENT_ESEWA_PRODUCT_CODE,
        "product_service_charge": "0",
        "product_delivery_charge": "0",
        "success_url": success_url,
        "failure_url": failure_url,
        "signed_field_names": "total_amount,transaction_uuid,product_code",
    }
    fields["signature"] = _sign_esewa_fields(
        fields, fields["signed_field_names"].split(",")
    )
    return fields


def _verify_esewa_return(payment, encoded_response):
    try:
        decoded = base64.b64decode(encoded_response, validate=True)
        response_data = json.loads(decoded.decode("utf-8"))
        signed_field_names = response_data["signed_field_names"]
        if not isinstance(signed_field_names, str):
            return False
        signed_names = signed_field_names.split(",")
        signature = response_data["signature"]
        if not signed_names or any(name not in response_data for name in signed_names):
            return False
        expected_signature = _sign_esewa_fields(response_data, signed_names)
        if not hmac.compare_digest(signature, expected_signature):
            return False
        amount = _parse_amount(response_data["total_amount"])
        return (
            amount is not None
            and response_data.get("transaction_uuid") == str(payment.order_id)
            and response_data.get("product_code") == settings.PAYMENT_ESEWA_PRODUCT_CODE
            and amount == payment.amount
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return False


def _esewa_status(payment):
    query = urlencode(
        {
            "product_code": settings.PAYMENT_ESEWA_PRODUCT_CODE,
            "total_amount": f"{payment.amount:.2f}",
            "transaction_uuid": str(payment.order_id),
        }
    )
    request = Request(f"{ESEWA_STATUS_URL}?{query}", method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        logger.warning("eSewa status lookup returned HTTP %s.", exc.code)
        raise PaymentGatewayError("Could not verify the payment with eSewa.") from exc
    except (URLError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        logger.warning("eSewa status lookup failed: %s", reason)
        raise PaymentGatewayError("Could not reach eSewa to verify the payment.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("eSewa status lookup returned an invalid response.")
        raise PaymentGatewayError(
            "eSewa returned an invalid verification response."
        ) from exc
    if not isinstance(data, dict):
        raise PaymentGatewayError("eSewa returned an invalid verification response.")
    return data


def initiate_khalti(payment, return_url, website_url, customer_info=None):
    if not gateway_is_configured(Payment.Provider.KHALTI):
        raise PaymentGatewayError("Khalti sandbox is not configured.")
    amount_paisa = int(payment.amount * 100)
    payload = {
        "return_url": return_url,
        "website_url": website_url,
        "amount": amount_paisa,
        "purchase_order_id": str(payment.order_id),
        "purchase_order_name": payment.plan.name,
    }
    if customer_info:
        payload["customer_info"] = customer_info
    data = _request_json(
        url=f"{KHALTI_API_URL}/initiate/",
        payload=payload,
        secret=settings.PAYMENT_KHALTI_SECRET_KEY,
    )
    pidx = data.get("pidx")
    payment_url = data.get("payment_url")
    parsed_url = urlparse(payment_url or "")
    if (
        not isinstance(pidx, str)
        or not pidx
        or len(pidx) > 120
        or parsed_url.scheme != "https"
        or parsed_url.hostname not in {"test-pay.khalti.com", "dev.khalti.com"}
    ):
        raise PaymentGatewayError("Khalti returned an invalid checkout response.")
    payment.provider_payment_id = pidx
    try:
        payment.save(update_fields=["provider_payment_id", "updated_at"])
    except IntegrityError as exc:
        raise PaymentGatewayError(
            "Khalti returned a payment reference already linked to another order."
        ) from exc
    return payment_url


def _lookup_khalti(payment):
    if not gateway_is_configured(Payment.Provider.KHALTI):
        raise PaymentGatewayError("Khalti sandbox is not configured.")
    return _request_json(
        url=f"{KHALTI_API_URL}/lookup/",
        payload={"pidx": payment.provider_payment_id},
        secret=settings.PAYMENT_KHALTI_SECRET_KEY,
    )


def _confirm_payment(payment, *, status, transaction_id=None, message=""):
    with transaction.atomic():
        locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked_payment.status != Payment.Status.PENDING:
            return locked_payment
        locked_payment.status = status
        locked_payment.transaction_id = transaction_id or None
        locked_payment.status_message = message[:240]
        update_fields = ["status", "transaction_id", "status_message", "updated_at"]
        if status == Payment.Status.SUCCEEDED:
            if not transaction_id or len(transaction_id) > 120:
                raise PaymentGatewayError(
                    "The provider did not return a valid transaction ID."
                )
            duplicate_transaction = Payment.objects.filter(
                transaction_id=transaction_id
            ).exclude(pk=locked_payment.pk)
            if duplicate_transaction.exists():
                locked_payment.status = Payment.Status.FAILED
                locked_payment.status_message = (
                    "This provider transaction ID is already linked to another payment."
                )
                locked_payment.save(
                    update_fields=["status", "status_message", "updated_at"]
                )
                Notification.objects.create(
                    user=locked_payment.user,
                    message=(
                        f"Payment for {locked_payment.plan_name or locked_payment.plan.name} "
                        "could not be verified because its transaction ID was already used."
                    ),
                )
                return locked_payment
            get_user_model().objects.select_for_update().get(
                pk=locked_payment.user_id
            )
            now = timezone.now()
            locked_payment.paid_at = now
            update_fields.append("paid_at")
            try:
                subscription = Subscription.objects.select_for_update().get(
                    user=locked_payment.user
                )
            except Subscription.DoesNotExist:
                Subscription.objects.create(
                    user=locked_payment.user,
                    plan=locked_payment.plan,
                    starts_at=now,
                    expires_at=now
                    + timezone.timedelta(days=locked_payment.duration_days),
                )
            else:
                start_at = max(subscription.expires_at, now)
                subscription.plan = locked_payment.plan
                subscription.starts_at = now
                subscription.expires_at = start_at + timezone.timedelta(
                    days=locked_payment.duration_days
                )
                subscription.save(
                    update_fields=["plan", "starts_at", "expires_at", "updated_at"]
                )
        locked_payment.save(update_fields=update_fields)
        if status == Payment.Status.SUCCEEDED:
            Notification.objects.create(
                user=locked_payment.user,
                message=(
                    f"Payment of NPR {locked_payment.amount:.2f} for "
                    f"{locked_payment.plan_name or locked_payment.plan.name} was verified. "
                    "Your subscription is active."
                ),
            )
        elif status == Payment.Status.FAILED:
            Notification.objects.create(
                user=locked_payment.user,
                message=(
                    f"Payment for {locked_payment.plan_name or locked_payment.plan.name} "
                    "was not completed. Check payment history for details."
                ),
            )
        return locked_payment


def verify_payment(payment, *, esewa_response=None):
    if payment.status != Payment.Status.PENDING:
        return payment
    if not gateway_is_configured(payment.provider):
        raise PaymentGatewayError("Payment sandbox is not enabled or configured.")

    if payment.provider == Payment.Provider.ESEWA:
        if esewa_response and not _verify_esewa_return(payment, esewa_response):
            raise PaymentGatewayError(
                "The eSewa return could not be verified; the payment remains pending."
            )
        data = _esewa_status(payment)
        try:
            verified_amount = _parse_amount(data["total_amount"])
            matches = (
                verified_amount is not None
                and data["transaction_uuid"] == str(payment.order_id)
                and data["product_code"] == settings.PAYMENT_ESEWA_PRODUCT_CODE
                and verified_amount == payment.amount
            )
        except (KeyError, TypeError, ValueError):
            raise PaymentGatewayError("eSewa returned incomplete verification details.")
        if not matches:
            return _confirm_payment(
                payment,
                status=Payment.Status.FAILED,
                message="The eSewa payment details did not match this order.",
            )
        status = data.get("status")
        if status == "COMPLETE":
            transaction_id = data.get("ref_id")
            if not isinstance(transaction_id, str) or not transaction_id:
                raise PaymentGatewayError(
                    "eSewa did not return a transaction reference for this payment."
                )
            return _confirm_payment(
                payment,
                status=Payment.Status.SUCCEEDED,
                transaction_id=transaction_id,
                message="Payment verified by eSewa.",
            )
        if status in {"PENDING", "AMBIGUOUS"}:
            payment.status_message = "eSewa is still processing this payment."
            payment.save(update_fields=["status_message", "updated_at"])
            return payment
        if status in {"NOT_FOUND", "CANCELED", "FAILED"}:
            return _confirm_payment(
                payment,
                status=Payment.Status.FAILED,
                message=f"eSewa reported {status.lower().replace('_', ' ')}.",
            )
        raise PaymentGatewayError("eSewa returned an unrecognized payment status.")

    if payment.provider == Payment.Provider.KHALTI:
        if not payment.provider_payment_id:
            raise PaymentGatewayError("The Khalti payment reference is missing.")
        data = _lookup_khalti(payment)
        try:
            verified_amount = _parse_amount(data["total_amount"])
            matches = (
                verified_amount is not None
                and verified_amount == verified_amount.to_integral_value()
                and data["pidx"] == payment.provider_payment_id
                and int(verified_amount) == int(payment.amount * 100)
                and data.get("purchase_order_id", str(payment.order_id))
                == str(payment.order_id)
            )
        except (KeyError, TypeError, ValueError):
            raise PaymentGatewayError("Khalti returned incomplete verification details.")
        if not matches:
            return _confirm_payment(
                payment,
                status=Payment.Status.FAILED,
                message="The Khalti payment details did not match this order.",
            )
        status = data.get("status")
        if status == "Completed" and not data.get("refunded", False):
            transaction_id = data.get("transaction_id")
            if not isinstance(transaction_id, str) or not transaction_id:
                raise PaymentGatewayError(
                    "Khalti did not return a transaction reference for this payment."
                )
            return _confirm_payment(
                payment,
                status=Payment.Status.SUCCEEDED,
                transaction_id=transaction_id,
                message="Payment verified by Khalti.",
            )
        if status in {"Pending", "Initiated"}:
            payment.status_message = "Khalti is still processing this payment."
            payment.save(update_fields=["status_message", "updated_at"])
            return payment
        if status in {"Expired", "User canceled", "Refunded"}:
            return _confirm_payment(
                payment,
                status=Payment.Status.FAILED,
                transaction_id=data.get("transaction_id"),
                message=f"Khalti reported {status.lower()}.",
            )
        raise PaymentGatewayError("Khalti returned an unrecognized payment status.")

    raise PaymentGatewayError("Unknown payment provider.")
