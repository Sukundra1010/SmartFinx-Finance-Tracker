live website ma gari dinu
# smartFinX — Personal Finance & Expense Tracker

A responsive Django application called **smartFinX** for managing personal income, expenses, monthly
budgets, savings goals, analytics, notifications, and downloadable reports.
Financial records are stored in SQLite and scoped to their owning user.

**Live app:** [https://sukundra1010.pythonanywhere.com](https://sukundra1010.pythonanywhere.com/)

## Features

- User registration, sign-in/sign-out, profile photo and detail editing, and password changes
- Private income and expense transactions with search, filters, sorting, and pagination
- Decimal-based financial amounts, defaulted to Nepalese Rupees (NPR)
- Monthly category budgets with utilization tracking and 80% / exceeded alerts
- Savings goals with dated contributions and completion notifications
- Dashboard summaries, six-month trends, expense charts, year-selectable profit/loss,
  all-time totals, and transaction history
- Custom and daily, weekly, monthly, or yearly reports; CSV, PDF, and print views
- Admin-managed SmartFinX subscription plans with provider-verified eSewa/Khalti sandbox
  payments, receipts, payment history, and CSV reports
- Responsive layout with light/dark theme preference and a local finance insight guide
- Installable Progressive Web App (PWA) with a safe offline screen; account and
  financial data are never stored for offline use
- Django admin for application administration

## Requirements

- Python 3.10 or newer
- Windows PowerShell (commands below use Windows paths)
- Internet access for the Chart.js CDN used by interactive charts

## Install

From the repository folder:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell prevents environment activation, call the environment executable
directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Initialize and run

```powershell
python manage.py check
python manage.py migrate
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and create an account. The SQLite database is
stored in `db.sqlite3` and persists between runs. The admin site is at
<http://127.0.0.1:8000/admin/>; create an administrator with:

```powershell
python manage.py createsuperuser
```

On a supported mobile browser, use **Install app** when it appears or the
browser's **Add to Home Screen** option. PWA installation requires HTTPS in
production (or localhost during development). SmartFinX deliberately caches
only its generic offline screen; financial pages and account data require a
network connection.

The public landing page is at `/`. After signing in, use `/dashboard/` to
manage finances; logging out returns to the landing page.

## Subscription payments (sandbox only)

The payment area is available at `/payments/` after sign-in. Administrators
create subscription plans, prices, and durations in Django admin. A subscription
is activated or extended only after the backend verifies the provider's final
status and amount. Receipts and CSV reports are limited to the signed-in user's
payments. Payment records are stored in three additional tables; the existing
SQLite database and finance tables are not replaced.

Payments are **disabled by default**, and this integration supports only the
providers' sandbox/test environments. It intentionally rejects any
`PAYMENTS_MODE` value other than `disabled` or `sandbox`; real/live payments are
not enabled by this project. Sandbox checkout can be configured through the
server environment:

```text
PAYMENTS_MODE=sandbox
PAYMENT_ESEWA_PRODUCT_CODE=EPAYTEST
PAYMENT_ESEWA_SECRET_KEY=<eSewa sandbox merchant secret>
PAYMENT_KHALTI_SECRET_KEY=<Khalti test merchant secret>
```

Set only the credentials for the provider being tested. Obtain eSewa UAT
credentials and Khalti test merchant credentials from their official developer
portals. Keep credentials in the deployment's private environment settings;
never put them in Git, source code, browser JavaScript, or payment records.
Users enter provider passwords, PINs, and OTPs only on the provider's page;
SmartFinX does not collect or store those credentials.

After setting environment variables, restart the app and run
`python manage.py migrate` to add the payment tables without replacing existing
data. Sign in as an administrator, add an active plan in **Subscription plans**,
then test checkout and receipt/history behavior with sandbox accounts:

- [eSewa ePay developer documentation](https://developer.esewa.com.np/pages/Epay)
- [Khalti ePayment documentation](https://docs.khalti.com/khalti-epayment/)
- [eSewa sandbox checkout](https://rc-epay.esewa.com.np/api/epay/main/v2/form)
- [Khalti sandbox merchant signup](https://test-admin.khalti.com/#/join/merchant)

The return URL is never treated as proof of payment: eSewa responses are
signature-checked and reconciled with its status API, and Khalti callbacks are
reconciled through its lookup API. Repeated callbacks do not activate or extend a
subscription twice. Verified success and failure create an in-app notification.
External calls are covered by mocked tests; complete
provider sandbox checkout still requires merchant test credentials and a
reachable deployment.

Uploaded profile photos are stored locally under `media/` during development.
The local database, uploads, virtual environments, and `.env` files are excluded
from Git. Run migrations after cloning to create a fresh database.

## Contributor

- [Sukundra1010](https://github.com/Sukundra1010)

## Tests

Run the integration tests with:

```powershell
python manage.py test finance
```

The suite covers registration, dashboard calculations, user-level data access,
transaction CRUD and filters, budget alerts, savings goals, and CSV/PDF exports.
It also tests disabled-by-default checkout, provider verification, duplicate
callbacks, subscription activation, user-scoped receipts/reports, and payment
notifications without making real gateway requests.

## Production settings

The included default settings are for local development. Before deployment,
provide a private `DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=false`, and set
`DJANGO_ALLOWED_HOSTS` to a comma-separated list of valid hostnames. HTTPS
redirect and secure session/CSRF cookies are enabled when debug mode is off.
Set `DJANGO_SECURE_SSL_REDIRECT=false` only when TLS termination is handled in a
way that makes Django's HTTPS detection incompatible; configure trusted proxy
headers correctly before doing so. Never deploy with the development secret key.

## Deploy publicly on Render

The root `render.yaml` Blueprint configures a Django web service, managed
PostgreSQL database, and persistent disk for uploaded profile photos. To create
the public app:

1. Push this repository to GitHub and sign in to [Render](https://render.com/).
2. In Render, choose **New → Blueprint**, connect
   `Sukundra1010/SmartFinx-Finance-Tracker`, and deploy the `main` branch.
3. Wait for the build and health check to succeed. Render assigns the app URL
   `https://smartfinx.onrender.com`.

The Blueprint uses paid Render plans because persistent profile uploads require
a disk and a durable production database. Review the current plan prices shown
by Render before confirming deployment. It starts with a new PostgreSQL
database; local `db.sqlite3` data and local profile photos are deliberately not
uploaded. The app generates a private Django secret automatically, applies
migrations during deployment, and serves collected static assets with
WhiteNoise. Do not commit the local database, media files, or secret values.

## Free public deployment on PythonAnywhere

PythonAnywhere offers a limited free account with one public
`<your-username>.pythonanywhere.com` web app. Its current limits include 512 MB
of storage, 100 CPU-seconds per day, and restricted outbound internet. That is
for trying out or a small demo, not guaranteed production capacity. HTTPS is
available. See the [current plan details](https://www.pythonanywhere.com/pricing/).

1. Create a free [PythonAnywhere Beginner account](https://www.pythonanywhere.com/registration/register/beginner/).
2. In its Bash console, clone this repository and install dependencies:

   ```bash
   git clone https://github.com/Sukundra1010/SmartFinx-Finance-Tracker.git
   cd SmartFinx-Finance-Tracker
   python3.13 -m venv ~/.virtualenvs/smartfinx
   source ~/.virtualenvs/smartfinx/bin/activate
   pip install -r requirements.txt
   mkdir -p ~/smartfinx-media
   ```

3. Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
   In the PythonAnywhere **Web** tab, create a manual-config web app using the
   same Python version. Set its virtualenv to
   `/home/<your-username>/.virtualenvs/smartfinx`, then open its WSGI file and
   set the following values before loading the Django application:

   ```python
   import os
   import sys

   project = "/home/<your-username>/SmartFinx-Finance-Tracker"
   sys.path.insert(0, project)
   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
   os.environ["DJANGO_DEBUG"] = "false"
   os.environ["DJANGO_SECRET_KEY"] = "<paste-the-generated-secret>"
   os.environ["DJANGO_ALLOWED_HOSTS"] = "<your-username>.pythonanywhere.com"
   os.environ["DJANGO_CSRF_TRUSTED_ORIGINS"] = "https://<your-username>.pythonanywhere.com"
   os.environ["DATABASE_URL"] = "sqlite:////home/<your-username>/.local/share/smartfinx/db.sqlite3"
   os.environ["DJANGO_MEDIA_ROOT"] = "/home/<your-username>/smartfinx-media"

   import config.settings
   config.settings.STORAGES["staticfiles"]["BACKEND"] = (
       "django.contrib.staticfiles.storage.StaticFilesStorage"
   )

   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

4. In the same Bash console and virtualenv, run
   `python manage.py migrate` and `python manage.py collectstatic --no-input`.
   In the Web tab, map `/static/` to
   `/home/<your-username>/SmartFinx-Finance-Tracker/staticfiles` and `/media/` to
   `/home/<your-username>/smartfinx-media`, then click **Reload**. The public address is
   `https://<your-username>.pythonanywhere.com` (for this project:
   [https://sukundra1010.pythonanywhere.com](https://sukundra1010.pythonanywhere.com/)).

The SQLite database and profile uploads stay in your account's home directory
and are not included in Git. They are separate from the local database and
photos on your computer. Replace `<your-username>` with your PythonAnywhere
username and never publish the generated secret. Free-plan limits apply.
