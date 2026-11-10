import csv
import json
from calendar import month_abbr, month_name
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .constants import CATEGORY_CHOICES
from .forms import (
    BudgetForm,
    PaymentInitiationForm,
    ProfileForm,
    RegistrationForm,
    SavingsContributionForm,
    SavingsGoalForm,
    TransactionForm,
)
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
from .payments import (
    ESEWA_CHECKOUT_URL,
    PaymentGatewayError,
    esewa_checkout_fields,
    gateway_is_configured,
    initiate_khalti,
    verify_payment,
)


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account is ready. Welcome!")
        return redirect("dashboard")

    return render(request, "finance/register.html", {"form": form})


def home(request):
    return render(request, "finance/home.html")


@login_required
def dashboard(request):
    today = timezone.localdate()
    current_month = Transaction.objects.filter(
        user=request.user,
        date__year=today.year,
        date__month=today.month,
    )
    income_result = current_month.filter(
        kind=Transaction.Kind.INCOME
    ).aggregate(total=Sum("amount"))
    income = income_result["total"] or Decimal("0.00")
    expenses = current_month.filter(kind=Transaction.Kind.EXPENSE).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    all_totals = (
        Transaction.objects.filter(user=request.user)
        .values("kind")
        .annotate(total=Sum("amount"))
    )
    totals = {
        row["kind"]: row["total"] or Decimal("0.00")
        for row in all_totals
    }
    balance = totals.get(Transaction.Kind.INCOME, Decimal("0.00")) - totals.get(
        Transaction.Kind.EXPENSE, Decimal("0.00")
    )
    budgets = Budget.objects.filter(
        user=request.user,
        year=today.year,
        month=today.month,
    )

    # Build chart values for the current month and the five months before it.
    month_labels = []
    income_series = []
    expense_series = []
    for offset in range(5, -1, -1):
        month_index = today.month - offset
        year = today.year
        while month_index <= 0:
            month_index += 12
            year -= 1
        month_rows = Transaction.objects.filter(
            user=request.user, date__year=year, date__month=month_index
        )
        month_labels.append(f"{month_abbr[month_index]} {year}")
        income_series.append(
            float(
                month_rows.filter(kind=Transaction.Kind.INCOME).aggregate(
                    total=Sum("amount")
                )["total"]
                or 0
            )
        )
        expense_series.append(
            float(
                month_rows.filter(kind=Transaction.Kind.EXPENSE).aggregate(
                    total=Sum("amount")
                )["total"]
                or 0
            )
        )

    category_totals = list(
        current_month.filter(kind=Transaction.Kind.EXPENSE)
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    category_colors = [
        "#27a875",
        "#438fe4",
        "#e8499d",
        "#8661df",
        "#f08332",
        "#ee5663",
        "#e3a817",
        "#9a72c5",
    ]
    category_expense_total = sum(
        (item["total"] for item in category_totals), Decimal("0.00")
    )
    for index, item in enumerate(category_totals):
        item["color"] = category_colors[index % len(category_colors)]
        item["percentage"] = (
            round(float(item["total"] / category_expense_total * 100))
            if category_expense_total
            else 0
        )
    goals = SavingsGoal.objects.filter(user=request.user).order_by("target_date")[:4]
    context = {
        "balance": balance,
        "monthly_income": income,
        "monthly_expenses": expenses,
        "monthly_savings": income - expenses,
        "monthly_budget": sum(
            (budget.amount for budget in budgets),
            Decimal("0.00"),
        ),
        "recent_transactions": Transaction.objects.filter(user=request.user)[:7],
        "budgets": budgets,
        "goals": goals,
        "month_labels_json": json.dumps(month_labels),
        "income_series_json": json.dumps(income_series),
        "expense_series_json": json.dumps(expense_series),
        "category_labels_json": json.dumps(
            [row["category"] for row in category_totals]
        ),
        "category_series_json": json.dumps(
            [float(row["total"]) for row in category_totals]
        ),
        "category_totals": category_totals,
        "savings_rate": (
            round(float((income - expenses) / income * 100), 1)
            if income
            else 0
        ),
        "monthly_net": income - expenses,
    }
    return render(request, "finance/dashboard.html", context)


@login_required
def payment_dashboard(request):
    payments = Payment.objects.filter(user=request.user).select_related("plan")
    status = request.GET.get("status", "")
    provider = request.GET.get("provider", "")
    if status in Payment.Status.values:
        payments = payments.filter(status=status)
    if provider in Payment.Provider.values:
        payments = payments.filter(provider=provider)
    page_obj = Paginator(payments, 15).get_page(request.GET.get("page"))
    previous_page_url = None
    next_page_url = None
    if page_obj.has_previous():
        previous_page_url = _payment_page_url(
            request, page_obj.previous_page_number()
        )
    if page_obj.has_next():
        next_page_url = _payment_page_url(request, page_obj.next_page_number())

    subscription = Subscription.objects.filter(user=request.user).select_related(
        "plan"
    ).first()
    form = PaymentInitiationForm()
    gateway_options = [
        (provider_value, provider_label, gateway_is_configured(provider_value))
        for provider_value, provider_label in Payment.Provider.choices
    ]
    context = {
        "page_obj": page_obj,
        "payments": page_obj.object_list,
        "previous_page_url": previous_page_url,
        "next_page_url": next_page_url,
        "form": form,
        "subscription": subscription,
        "payments_mode": settings.PAYMENTS_MODE,
        "gateway_options": gateway_options,
        "gateway_configured": any(option[2] for option in gateway_options),
        "selected_status": status,
        "selected_provider": provider,
        "status_choices": Payment.Status.choices,
        "provider_choices": Payment.Provider.choices,
    }
    return render(request, "finance/payments.html", context)


def _payment_page_url(request, page_number):
    query = request.GET.copy()
    query["page"] = page_number
    return f"?{query.urlencode()}"


@login_required
@require_POST
def payment_initiate(request):
    form = PaymentInitiationForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose an available plan and payment provider.")
        return redirect("payments")

    plan = form.cleaned_data["plan"]
    provider = form.cleaned_data["provider"]
    if not gateway_is_configured(provider):
        messages.error(
            request,
            (
                "Sandbox payments are currently disabled or not configured. "
                "No payment was created."
            ),
        )
        return redirect("payments")

    payment = Payment.objects.create(
        user=request.user,
        plan=plan,
        provider=provider,
        amount=plan.price,
        plan_name=plan.name,
        duration_days=plan.duration_days,
    )
    return_url = request.build_absolute_uri(
        reverse("payment_return", kwargs={"order_id": payment.order_id})
    )
    if provider == Payment.Provider.ESEWA:
        fields = esewa_checkout_fields(
            payment,
            success_url=return_url,
            failure_url=return_url,
        )
        return render(
            request,
            "finance/payment_esewa_checkout.html",
            {
                "payment": payment,
                "fields": fields,
                "gateway_url": ESEWA_CHECKOUT_URL,
            },
        )

    customer_info = None
    if request.user.email:
        customer_info = {
            "name": request.user.get_full_name() or request.user.get_username(),
            "email": request.user.email,
        }

    try:
        payment_url = initiate_khalti(
            payment,
            return_url=return_url,
            website_url=request.build_absolute_uri(reverse("home")),
            customer_info=customer_info,
        )
    except PaymentGatewayError as exc:
        payment.status = Payment.Status.FAILED
        payment.status_message = str(exc)[:240]
        payment.save(update_fields=["status", "status_message", "updated_at"])
        messages.error(request, str(exc))
        return redirect("payments")
    return redirect(payment_url)


@csrf_exempt
def payment_return(request, order_id):
    payment = get_object_or_404(
        Payment.objects.select_related("plan"),
        order_id=order_id,
    )
    callback_data = request.POST.get("data") or request.GET.get("data")
    try:
        payment = verify_payment(payment, esewa_response=callback_data)
    except PaymentGatewayError as exc:
        messages.warning(request, str(exc))
    else:
        if payment.status == Payment.Status.SUCCEEDED:
            messages.success(
                request,
                "Payment verified. Your subscription is active.",
            )
        elif payment.status == Payment.Status.FAILED:
            messages.error(
                request,
                "Payment was not completed. You have not been charged.",
            )
        else:
            messages.info(request, "Payment is still pending provider confirmation.")
    return redirect("payment_receipt", order_id=payment.order_id)


@login_required
def payment_receipt(request, order_id):
    payment = get_object_or_404(
        Payment.objects.select_related("plan", "user"),
        order_id=order_id,
        user=request.user,
    )
    return render(request, "finance/payment_receipt.html", {"payment": payment})


@login_required
def payment_report(request):
    payments = Payment.objects.filter(user=request.user).select_related("plan")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="smartfinx-payment-report.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "Order ID",
            "Plan",
            "Provider",
            "Status",
            "Amount (NPR)",
            "Provider transaction ID",
            "Created at",
            "Paid at",
        ]
    )
    for payment in payments:
        writer.writerow(
            [
                payment.order_id,
                safe_csv_field(payment.plan_name or payment.plan.name),
                payment.get_provider_display(),
                payment.get_status_display(),
                f"{payment.amount:.2f}",
                safe_csv_field(payment.transaction_id or ""),
                payment.created_at.isoformat(),
                payment.paid_at.isoformat() if payment.paid_at else "",
            ]
        )
    return response


@login_required
def transaction_list(request):
    queryset = Transaction.objects.filter(user=request.user)
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    category = request.GET.get("category", "")
    payment_method = request.GET.get("payment_method", "")
    start = parse_date(request.GET.get("start", ""))
    end = parse_date(request.GET.get("end", ""))
    if query:
        queryset = queryset.filter(title__icontains=query) | queryset.filter(
            description__icontains=query
        )
        queryset = queryset.filter(user=request.user)
    if kind in Transaction.Kind.values:
        queryset = queryset.filter(kind=kind)
    if category:
        queryset = queryset.filter(category=category)
    payment_method_choices = [
        choice
        for choice in Transaction.PaymentMethod.choices
        if choice[0] not in {"card", "bank", "wallet"}
    ] + [
        choice
        for choice in Transaction.PaymentMethod.choices
        if choice[0] in {"card", "bank", "wallet"}
    ]
    if payment_method in dict(payment_method_choices):
        queryset = queryset.filter(payment_method=payment_method)
    if start:
        queryset = queryset.filter(date__gte=start)
    if end:
        queryset = queryset.filter(date__lte=end)
    sort = request.GET.get("sort", "newest")
    sort_options = {
        "oldest": "date",
        "amount": "-amount",
        "-amount": "amount",
    }
    ordering = sort_options.get(sort, "-date")
    queryset = queryset.order_by(ordering, "-created_at")
    page_obj = Paginator(queryset, 15).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/transactions.html",
        {
            "page_obj": page_obj,
            "transactions": page_obj.object_list,
            "query": query,
            "selected_kind": kind,
            "selected_category": category,
            "selected_payment_method": payment_method,
            "category_choices": CATEGORY_CHOICES,
            "payment_method_choices": payment_method_choices,
            "selected_start": request.GET.get("start", ""),
            "selected_end": request.GET.get("end", ""),
            "selected_sort": sort,
        },
    )


def budget_spend(user, category, year, month):
    expenses = Transaction.objects.filter(
        user=user,
        kind=Transaction.Kind.EXPENSE,
        category=category,
        date__year=year,
        date__month=month,
    )
    result = expenses.aggregate(total=Sum("amount"))
    return result["total"] or Decimal("0.00")


def notify_budget_crossing(user, category, year, month, before, limit):
    after = budget_spend(user, category, year, month)
    alert_threshold = limit * Decimal("0.80")

    if before < alert_threshold <= after:
        Notification.objects.create(
            user=user,
            message=f"{category} budget is at least 80% used ({year}-{month:02}).",
        )
    if before <= limit < after:
        Notification.objects.create(
            user=user,
            message=f"{category} budget exceeded ({year}-{month:02}).",
        )


@login_required
def transaction_create(request):
    form = TransactionForm(
        request.POST or None,
        initial={"date": timezone.localdate()},
    )
    if request.method == "POST" and form.is_valid():
        transaction = form.save(commit=False)
        transaction.user = request.user
        budget = Budget.objects.filter(
            user=request.user,
            category=transaction.category,
            year=transaction.date.year,
            month=transaction.date.month,
        ).first()
        before = (
            budget_spend(
                request.user,
                transaction.category,
                transaction.date.year,
                transaction.date.month,
            )
            if budget and transaction.kind == Transaction.Kind.EXPENSE
            else Decimal("0.00")
        )
        transaction.save()
        if budget and transaction.kind == Transaction.Kind.EXPENSE:
            notify_budget_crossing(
                request.user,
                transaction.category,
                transaction.date.year,
                transaction.date.month,
                before,
                budget.amount,
            )
        messages.success(request, f"{transaction.get_kind_display()} saved.")
        return redirect("transactions")
    return render(
        request,
        "finance/form.html",
        {"form": form, "title": "Add transaction", "cancel_url": "transactions"},
    )


@login_required
def transaction_edit(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk, user=request.user)
    form = TransactionForm(request.POST or None, instance=transaction)
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        updated.user = request.user
        budget = None
        if updated.kind == Transaction.Kind.EXPENSE:
            budget = Budget.objects.filter(
                user=request.user,
                category=updated.category,
                year=updated.date.year,
                month=updated.date.month,
            ).first()
        before = (
            budget_spend(
                request.user,
                updated.category,
                updated.date.year,
                updated.date.month,
            )
            if budget
            else Decimal("0.00")
        )
        updated.save()
        if budget:
            notify_budget_crossing(
                request.user,
                updated.category,
                updated.date.year,
                updated.date.month,
                before,
                budget.amount,
            )
        messages.success(request, "Transaction updated.")
        return redirect("transactions")
    return render(
        request,
        "finance/form.html",
        {"form": form, "title": "Edit transaction", "cancel_url": "transactions"},
    )


@login_required
@require_POST
def transaction_delete(request, pk):
    transaction = get_object_or_404(Transaction, pk=pk, user=request.user)
    transaction.delete()
    messages.success(request, "Transaction deleted.")
    return redirect("transactions")


@login_required
def budget_list(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        if not 2000 <= year <= 9998 or not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month
    budget_date = date(year, month, 1)
    previous_date = budget_date - timedelta(days=1)
    next_date = date(year + (month == 12), month % 12 + 1, 1)
    budgets = Budget.objects.filter(
        user=request.user,
        year=year,
        month=month,
    )
    return render(
        request,
        "finance/budgets.html",
        {
            "budgets": budgets,
            "current_year": year,
            "current_month": month,
            "budget_month_name": month_name[month],
            "previous_date": previous_date,
            "next_date": next_date,
        },
    )


@login_required
def budget_create(request):
    today = timezone.localdate()
    try:
        initial_year = int(request.GET.get("year", today.year))
        initial_month = int(request.GET.get("month", today.month))
        if not 2000 <= initial_year <= 9998 or not 1 <= initial_month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        initial_year, initial_month = today.year, today.month
    form = BudgetForm(
        request.POST or None,
        user=request.user,
        initial={"year": initial_year, "month": initial_month},
    )
    if request.method == "POST" and form.is_valid():
        budget = form.save(commit=False)
        budget.user = request.user
        budget.save()
        messages.success(request, "Monthly budget created.")
        if budget.year == today.year and budget.month == today.month:
            return redirect("dashboard")
        return redirect(
            f"{reverse('budgets')}?year={budget.year}&month={budget.month}"
        )
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": "Create budget",
            "submit_label": "Create budget",
            "cancel_url": "budgets",
        },
    )


@login_required
def budget_edit(request, pk):
    budget = get_object_or_404(Budget, pk=pk, user=request.user)
    form = BudgetForm(request.POST or None, user=request.user, instance=budget)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        item.save()
        messages.success(request, "Budget updated.")
        return redirect("budgets")
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": "Edit budget",
            "submit_label": "Save budget",
            "cancel_url": "budgets",
        },
    )


@login_required
@require_POST
def budget_delete(request, pk):
    get_object_or_404(Budget, pk=pk, user=request.user).delete()
    messages.success(request, "Budget deleted.")
    return redirect("budgets")


@login_required
def savings_list(request):
    goals = (
        SavingsGoal.objects.filter(user=request.user)
        .prefetch_related("contributions")
    )
    return render(
        request,
        "finance/savings.html",
        {"goals": goals},
    )


@login_required
def savings_create(request):
    form = SavingsGoalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        goal = form.save(commit=False)
        goal.user = request.user
        goal.save()
        messages.success(request, "Savings goal created.")
        return redirect("savings")
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": "Create savings goal",
            "cancel_url": "savings",
        },
    )


@login_required
def savings_edit(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    form = SavingsGoalForm(request.POST or None, instance=goal)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        item.save()
        refresh_goal_completion(item)
        messages.success(request, "Savings goal updated.")
        return redirect("savings")
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": "Edit savings goal",
            "cancel_url": "savings",
        },
    )


@login_required
@require_POST
def savings_delete(request, pk):
    get_object_or_404(SavingsGoal, pk=pk, user=request.user).delete()
    messages.success(request, "Savings goal deleted.")
    return redirect("savings")


@login_required
def contribution_create(request, pk):
    goal = get_object_or_404(SavingsGoal, pk=pk, user=request.user)
    form = SavingsContributionForm(
        request.POST or None,
        initial={"date": timezone.localdate()},
    )
    if request.method == "POST" and form.is_valid():
        contribution = form.save(commit=False)
        contribution.goal = goal
        contribution.save()
        refresh_goal_completion(goal)
        messages.success(request, "Savings contribution added.")
        return redirect("savings")
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": f"Contribute to {goal.name}",
            "cancel_url": "savings",
        },
    )


@login_required
def contribution_edit(request, pk):
    contribution = get_object_or_404(
        SavingsContribution.objects.select_related("goal"),
        pk=pk,
        goal__user=request.user,
    )
    form = SavingsContributionForm(request.POST or None, instance=contribution)
    if request.method == "POST" and form.is_valid():
        form.save()
        refresh_goal_completion(contribution.goal)
        messages.success(request, "Contribution updated.")
        return redirect("savings")
    return render(
        request,
        "finance/form.html",
        {
            "form": form,
            "title": "Edit contribution",
            "cancel_url": "savings",
        },
    )


@login_required
@require_POST
def contribution_delete(request, pk):
    contribution = get_object_or_404(
        SavingsContribution.objects.select_related("goal"),
        pk=pk,
        goal__user=request.user,
    )
    goal = contribution.goal
    contribution.delete()
    refresh_goal_completion(goal)
    messages.success(request, "Contribution deleted.")
    return redirect("savings")


def refresh_goal_completion(goal):
    complete = goal.saved_amount >= goal.target_amount
    if goal.completed != complete:
        goal.completed = complete
        goal.save(update_fields=["completed"])
        if complete:
            Notification.objects.create(
                user=goal.user,
                message=f"Savings goal completed: {goal.name}.",
            )


@login_required
def analytics(request):
    today = timezone.localdate()
    all_records = Transaction.objects.filter(user=request.user)
    available_years = [
        value.year for value in all_records.dates("date", "year", order="DESC")
    ]
    years = sorted(set(available_years) | {today.year}, reverse=True)
    try:
        requested_year = int(request.GET.get("year", today.year))
    except (TypeError, ValueError):
        requested_year = today.year
    year = requested_year if requested_year in years else years[0]
    labels = [month_abbr[month] for month in range(1, 13)]
    income = []
    expenses = []
    for month in range(1, 13):
        month_records = Transaction.objects.filter(
            user=request.user,
            date__year=year,
            date__month=month,
        )
        monthly_income = month_records.filter(kind="income").aggregate(
            total=Sum("amount")
        )["total"] or 0
        monthly_expenses = month_records.filter(kind="expense").aggregate(
            total=Sum("amount")
        )["total"] or 0

        income.append(float(monthly_income))
        expenses.append(float(monthly_expenses))
    categories = list(
        Transaction.objects.filter(
            user=request.user,
            kind="expense",
            date__year=year,
        )
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    category_colors = [
        "#27a875",
        "#438fe4",
        "#e8499d",
        "#8661df",
        "#f08332",
        "#ee5663",
        "#e3a817",
        "#9a72c5",
    ]
    category_total = sum(
        (item["total"] for item in categories),
        Decimal("0.00"),
    )
    for index, item in enumerate(categories):
        item["color"] = category_colors[index % len(category_colors)]
        item["percentage"] = (
            round(float(item["total"] / category_total * 100))
            if category_total
            else 0
        )
    all_year = all_records.filter(date__year=year)
    payment_method_totals_by_value = {
        item["payment_method"]: item["total"]
        for item in all_year.filter(kind=Transaction.Kind.EXPENSE)
        .values("payment_method")
        .annotate(total=Sum("amount"))
    }
    payment_method_groups = [
        ("Cash", ["cash"], "#27a875"),
        ("Bank Transfer", ["bank_transfer", "bank"], "#438fe4"),
        ("eSewa", ["esewa"], "#e8499d"),
        ("Khalti", ["khalti"], "#8661df"),
        ("IME Pay", ["ime_pay"], "#f08332"),
        ("Debit Card", ["debit_card"], "#ee5663"),
        ("Credit Card", ["credit_card", "card"], "#e3a817"),
        ("Online Payment", ["online_payment"], "#9a72c5"),
        ("Other", ["other"], "#4d9b91"),
        ("Digital wallet (legacy)", ["wallet"], "#8b9690"),
        ("Not specified", [""], "#bdc5c0"),
    ]
    payment_method_chart = [
        {
            "label": label,
            "total": sum(
                (payment_method_totals_by_value.get(value, Decimal("0.00")) for value in values),
                Decimal("0.00"),
            ),
            "color": color,
        }
        for label, values, color in payment_method_groups
    ]
    payment_method_chart = [
        item for item in payment_method_chart if item["total"] > 0
    ]
    payment_method_summary = [
        {
            "label": label,
            "total": sum(
                (payment_method_totals_by_value.get(value, Decimal("0.00")) for value in values),
                Decimal("0.00"),
            ),
        }
        for label, values in [
            ("Cash", ["cash"]),
            ("eSewa", ["esewa"]),
            ("Khalti", ["khalti"]),
            ("Bank Transfer", ["bank_transfer", "bank"]),
            ("Card", ["debit_card", "credit_card", "card"]),
        ]
    ]
    year_totals = all_year.aggregate(
        income=Sum("amount", filter=Q(kind=Transaction.Kind.INCOME)),
        expenses=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)),
        transaction_count=Count("pk"),
    )
    year_income = year_totals["income"] or Decimal("0.00")
    year_expenses = year_totals["expenses"] or Decimal("0.00")
    lifetime_totals = all_records.aggregate(
        income=Sum("amount", filter=Q(kind=Transaction.Kind.INCOME)),
        expenses=Sum("amount", filter=Q(kind=Transaction.Kind.EXPENSE)),
        transaction_count=Count("pk"),
    )
    all_time_income = lifetime_totals["income"] or Decimal("0.00")
    all_time_expenses = lifetime_totals["expenses"] or Decimal("0.00")
    history_page = Paginator(
        all_year.order_by("-date", "-created_at"), 15
    ).get_page(request.GET.get("history_page"))
    insights = []
    if categories:
        leading = categories[0]
        insights.append(
            f"{leading['category']} is your largest expense category this year at "
            f"Rs. {leading['total']:,.2f}."
        )
    if year_income:
        savings_rate = (year_income - year_expenses) / year_income * 100
        if savings_rate >= 0:
            insights.append(
                f"You have saved {savings_rate:.1f}% of your income this year."
            )
        else:
            insights.append(
                f"Your expenses are {abs(savings_rate):.1f}% higher than your income this year."
            )
    current_budgets = Budget.objects.filter(
        user=request.user,
        year=today.year,
        month=today.month,
    )
    for budget in current_budgets:
        if budget.utilization >= 80:
            insights.append(
                f"{budget.category} is at {budget.utilization:.1f}% of its monthly budget."
            )
    context = {
        "year": year,
        "years": years,
        "labels_json": json.dumps(labels),
        "income_json": json.dumps(income),
        "expenses_json": json.dumps(expenses),
        "category_labels_json": json.dumps(
            [item["category"] for item in categories]
        ),
        "category_values_json": json.dumps(
            [float(item["total"]) for item in categories]
        ),
        "payment_method_labels_json": json.dumps(
            [item["label"] for item in payment_method_chart]
        ),
        "payment_method_values_json": json.dumps(
            [float(item["total"]) for item in payment_method_chart]
        ),
        "payment_method_colors_json": json.dumps(
            [item["color"] for item in payment_method_chart]
        ),
        "payment_method_chart": payment_method_chart,
        "payment_method_summary": payment_method_summary,
        "year_income": year_income,
        "year_expenses": year_expenses,
        "year_net": year_income - year_expenses,
        "year_net_amount": abs(year_income - year_expenses),
        "year_transaction_count": year_totals["transaction_count"],
        "all_time_income": all_time_income,
        "all_time_expenses": all_time_expenses,
        "all_time_net": all_time_income - all_time_expenses,
        "all_time_net_amount": abs(all_time_income - all_time_expenses),
        "all_time_transaction_count": lifetime_totals["transaction_count"],
        "history_page": history_page,
        "history_transactions": history_page.object_list,
        "insights": insights,
        "category_totals": categories,
    }
    return render(request, "finance/analytics.html", context)


@login_required
def assistant(request):
    today = timezone.localdate()
    month_records = Transaction.objects.filter(
        user=request.user, date__year=today.year, date__month=today.month
    )
    month_income = month_records.filter(kind="income").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    month_expenses = month_records.filter(kind="expense").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    category_totals = list(
        month_records.filter(kind="expense")
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    budgets = Budget.objects.filter(
        user=request.user,
        year=today.year,
        month=today.month,
    )
    goals = SavingsGoal.objects.filter(user=request.user).order_by("target_date")
    question = ""
    answer_title = "Your monthly summary"
    answer = (
        f"You recorded Rs. {month_income:,.2f} in income and Rs. "
        f"{month_expenses:,.2f} in expenses this month. Your net is "
        f"Rs. {month_income - month_expenses:,.2f}."
    )
    if request.method == "POST":
        question = request.POST.get("question", "").strip()[:240]
        if not question:
            messages.error(request, "Enter a question to get an insight.")
        else:
            normalized = question.casefold()
            budget_words = ("budget", "limit", "left", "remaining")
            savings_words = ("save", "saving", "goal", "goals")
            spending_words = ("spend", "expense", "category", "spent")
            summary_words = ("income", "summary", "month", "balance")

            if any(word in normalized for word in budget_words):
                answer_title = "Your budget check-in"
                if budgets.exists():
                    over_limit = [budget for budget in budgets if budget.remaining < 0]
                    near_limit = [
                        budget
                        for budget in budgets
                        if budget.remaining >= 0 and budget.utilization >= 80
                    ]
                    if over_limit:
                        names = ", ".join(item.category for item in over_limit)
                        answer = (
                            f"{names} is over its monthly limit. "
                            "Review recent expenses or adjust your budget."
                        )
                    elif near_limit:
                        names = ", ".join(item.category for item in near_limit)
                        answer = (
                            f"{names} has used at least 80% of its budget. "
                            "You still have time to plan the rest of the month."
                        )
                    else:
                        remaining_total = sum(
                            (
                                max(budget.remaining, Decimal("0.00"))
                                for budget in budgets
                            ),
                            Decimal("0.00"),
                        )
                        answer = (
                            "Your budgets are below the 80% alert point. "
                            f"There is Rs. {remaining_total:,.2f} remaining "
                            "across your categories."
                        )
                else:
                    answer = (
                        "You haven't set a budget for this month yet. "
                        "Add category limits to get spending alerts."
                    )
            elif any(word in normalized for word in savings_words):
                answer_title = "Your savings progress"
                if goals.exists():
                    goal = min(goals, key=lambda item: item.progress)
                    answer = (
                        f"You have {goals.count()} active savings goal(s). "
                        f"'{goal.name}' is {goal.progress:.0f}% funded, with "
                        f"Rs. {goal.remaining_amount:,.2f} left to reach its target."
                    )
                else:
                    answer = (
                        "No savings goals yet. Create one with a target and date "
                        "to track your progress."
                    )
            elif any(word in normalized for word in spending_words):
                answer_title = "Your spending analysis"
                if category_totals:
                    top = category_totals[0]
                    answer = (
                        f"{top['category']} is your largest expense category this month "
                        f"at Rs. {top['total']:,.2f}. Total expenses so far are "
                        f"Rs. {month_expenses:,.2f}."
                    )
                else:
                    answer = (
                        "There are no expenses recorded this month yet. "
                        "Add transactions to see your spending patterns."
                    )
            elif any(word in normalized for word in summary_words):
                answer_title = "Your monthly summary"
            else:
                answer_title = "A quick financial check-in"
                answer = (
                    f"This month your income is Rs. {month_income:,.2f}, expenses are "
                    f"Rs. {month_expenses:,.2f}, and your net is "
                    f"Rs. {month_income - month_expenses:,.2f}. Ask about spending, "
                    f"budgets, or savings for a focused breakdown."
                )
    context = {
        "question": question,
        "answer_title": answer_title,
        "answer": answer,
        "month_income": month_income,
        "month_expenses": month_expenses,
        "month_net": month_income - month_expenses,
        "budgets": budgets,
        "goals": goals,
        "top_category": category_totals[0] if category_totals else None,
    }
    return render(request, "finance/assistant.html", context)


def report_queryset(request):
    today = timezone.localdate()
    preset = request.GET.get("period", "custom")
    if preset == "daily":
        start = end = today
    elif preset == "weekly":
        start = today - timedelta(days=today.weekday())
        end = today
    elif preset == "monthly":
        start = today.replace(day=1)
        end = today
    elif preset == "yearly":
        start = today.replace(month=1, day=1)
        end = today
    else:
        start_text = request.GET.get("start", "")
        end_text = request.GET.get("end", "")
        start = parse_date(start_text) if start_text else today.replace(day=1)
        end = parse_date(end_text) if end_text else today
    if not start or not end or start > end:
        return None, None, None
    queryset = Transaction.objects.filter(
        user=request.user,
        date__gte=start,
        date__lte=end,
    )
    kind = request.GET.get("kind", "")
    if kind in Transaction.Kind.values:
        queryset = queryset.filter(kind=kind)
    return queryset.order_by("date", "created_at"), start, end


@login_required
def reports(request):
    queryset, start, end = report_queryset(request)
    if queryset is None:
        messages.error(request, "Choose a valid date range with the start on or before the end.")
        queryset = Transaction.objects.none()
    summary = queryset.values("kind").annotate(total=Sum("amount"))
    totals = {item["kind"]: item["total"] or Decimal("0.00") for item in summary}
    return render(request, "finance/reports.html", {
        "transactions": queryset,
        "report_start": start or request.GET.get("start", ""),
        "report_end": end or request.GET.get("end", ""),
        "selected_kind": request.GET.get("kind", ""),
        "selected_period": request.GET.get("period", "custom"),
        "income_total": totals.get("income", Decimal("0.00")),
        "expense_total": totals.get("expense", Decimal("0.00")),
    })


@login_required
def report_csv(request):
    queryset, start, end = report_queryset(request)
    if queryset is None:
        messages.error(request, "Choose a valid date range before downloading.")
        return redirect("reports")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="finance-report-{start}-to-{end}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Date", "Type", "Title", "Category", "Payment method", "Amount", "Description"])
    for transaction in queryset:
        writer.writerow([
            safe_csv_field(transaction.date),
            safe_csv_field(transaction.get_kind_display()),
            safe_csv_field(transaction.title),
            safe_csv_field(transaction.category),
            safe_csv_field(transaction.get_payment_method_display()),
            f"{transaction.amount:.2f}",
            safe_csv_field(transaction.description),
        ])
    return response


def safe_csv_field(value):
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


@login_required
def report_pdf(request):
    queryset, start, end = report_queryset(request)
    if queryset is None:
        messages.error(request, "Choose a valid date range before downloading.")
        return redirect("reports")
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter, title="Personal finance report")
    styles = getSampleStyleSheet()
    income_total = queryset.filter(kind="income").aggregate(total=Sum("amount"))["total"] or Decimal("0")
    expense_total = queryset.filter(kind="expense").aggregate(total=Sum("amount"))["total"] or Decimal("0")
    content = [
        Paragraph("Personal Finance Report", styles["Title"]),
        Paragraph(
            f"{escape(request.user.get_full_name() or request.user.username)} · {start} to {end}",
            styles["Normal"],
        ),
        Spacer(1, 14),
        Paragraph(
            f"Income: Rs. {income_total:,.2f} &nbsp;&nbsp; "
            f"Expenses: Rs. {expense_total:,.2f} &nbsp;&nbsp; "
            f"Net: Rs. {income_total - expense_total:,.2f}",
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]
    rows = [["Date", "Type", "Title", "Category", "Amount"]]
    rows.extend([
        [str(item.date), item.get_kind_display(), item.title[:32], item.category, f"Rs. {item.amount:,.2f}"]
        for item in queryset
    ])
    table = Table(rows, repeatRows=1, colWidths=[58, 48, 158, 78, 82])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12261f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce4df")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f8f6")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    content.append(table)
    document.build(content)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="finance-report-{start}-to-{end}.pdf"'
    return response


@login_required
def profile(request):
    profile_record = request.user.profile
    form = ProfileForm(
        request.POST or None,
        request.FILES or None,
        instance=profile_record,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        old_image = profile_record.image
        updated_profile = form.save()
        if old_image and old_image.name != updated_profile.image.name:
            old_image.delete(save=False)
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "finance/profile.html", {"form": form})


@login_required
def notifications(request):
    return render(
        request,
        "finance/notifications.html",
        {"notifications": Notification.objects.filter(user=request.user)},
    )


@login_required
@require_POST
def notifications_read_all(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    messages.success(request, "Notifications marked as read.")
    return redirect("notifications")
