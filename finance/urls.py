from django.contrib.auth import views as auth_views
from django.urls import path

from . import views


urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("payments/", views.payment_dashboard, name="payments"),
    path("payments/initiate/", views.payment_initiate, name="payment_initiate"),
    path(
        "payments/<uuid:order_id>/return/",
        views.payment_return,
        name="payment_return",
    ),
    path(
        "payments/<uuid:order_id>/receipt/",
        views.payment_receipt,
        name="payment_receipt",
    ),
    path("payments/report.csv", views.payment_report, name="payment_report"),
    path("register/", views.register, name="register"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="finance/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password-change/",
        auth_views.PasswordChangeView.as_view(
            template_name="finance/password_change.html",
            success_url="/profile/?password_changed=1",
        ),
        name="password_change",
    ),
    path("profile/", views.profile, name="profile"),
    path("transactions/", views.transaction_list, name="transactions"),
    path("transactions/new/", views.transaction_create, name="transaction_create"),
    path(
        "transactions/<int:pk>/edit/",
        views.transaction_edit,
        name="transaction_edit",
    ),
    path(
        "transactions/<int:pk>/delete/",
        views.transaction_delete,
        name="transaction_delete",
    ),
    path("budgets/", views.budget_list, name="budgets"),
    path("budgets/new/", views.budget_create, name="budget_create"),
    path("budgets/<int:pk>/edit/", views.budget_edit, name="budget_edit"),
    path("budgets/<int:pk>/delete/", views.budget_delete, name="budget_delete"),
    path("savings/", views.savings_list, name="savings"),
    path("savings/new/", views.savings_create, name="savings_create"),
    path("savings/<int:pk>/edit/", views.savings_edit, name="savings_edit"),
    path("savings/<int:pk>/delete/", views.savings_delete, name="savings_delete"),
    path(
        "savings/<int:pk>/contribute/",
        views.contribution_create,
        name="contribution_create",
    ),
    path(
        "contributions/<int:pk>/edit/",
        views.contribution_edit,
        name="contribution_edit",
    ),
    path(
        "contributions/<int:pk>/delete/",
        views.contribution_delete,
        name="contribution_delete",
    ),
    path("analytics/", views.analytics, name="analytics"),
    path("assistant/", views.assistant, name="assistant"),
    path("reports/", views.reports, name="reports"),
    path("reports/csv/", views.report_csv, name="report_csv"),
    path("reports/pdf/", views.report_pdf, name="report_pdf"),
    path("notifications/", views.notifications, name="notifications"),
    path(
        "notifications/read-all/",
        views.notifications_read_all,
        name="notifications_read_all",
    ),
]
