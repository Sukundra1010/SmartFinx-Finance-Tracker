(() => {
  const root = document.documentElement;
  const savedTheme =
    localStorage.getItem("smartfinx-theme") ||
    localStorage.getItem("smartfinance-theme");

  if (savedTheme === "dark") {
    root.dataset.theme = "dark";
  }

  const themeToggle = document.getElementById("theme-toggle");
  const syncThemeToggle = () => {
    themeToggle?.setAttribute(
      "aria-pressed",
      String(root.dataset.theme === "dark"),
    );
  };

  syncThemeToggle();
  themeToggle?.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("smartfinx-theme", root.dataset.theme);
    syncThemeToggle();
  });

  const menuToggle = document.getElementById("menu-toggle");
  menuToggle?.addEventListener("click", () => {
    document.getElementById("sidebar")?.classList.toggle("sidebar-open");
  });

  const reportDateIds = ["report-start", "report-end"];
  reportDateIds.forEach((id) => {
    const dateInput = document.getElementById(id);
    dateInput?.addEventListener("change", () => {
      const periodSelect = document.getElementById("report-period");

      if (periodSelect) {
        periodSelect.value = "custom";
      }
    });
  });

  const categoryButtons = document.querySelectorAll("[data-category-pick]");
  const categorySelect = document.querySelector('select[name="category"]');

  categoryButtons.forEach((button) => {
    if (categorySelect?.value === button.dataset.categoryPick) {
      button.classList.add("selected");
    }

    button.addEventListener("click", () => {
      if (!categorySelect) {
        return;
      }

      categorySelect.value = button.dataset.categoryPick;
      categorySelect.dispatchEvent(new Event("change", { bubbles: true }));

      categoryButtons.forEach((categoryButton) => {
        categoryButton.classList.remove("selected");
      });

      button.classList.add("selected");
    });
  });

  const kindSelect = document.querySelector('select[name="kind"]');
  const kindButtons = document.querySelectorAll("[data-kind-pick]");

  kindButtons.forEach((button) => {
    if (kindSelect?.value === button.dataset.kindPick) {
      button.classList.add("selected");
    }

    button.addEventListener("click", () => {
      if (!kindSelect) {
        return;
      }

      kindSelect.value = button.dataset.kindPick;
      const defaultCategories = ["Food", "Salary", "Other"];
      const needsDefaultCategory =
        categorySelect && defaultCategories.includes(categorySelect.value);

      if (needsDefaultCategory) {
        categorySelect.value =
          button.dataset.kindPick === "income" ? "Salary" : "Food";
        categorySelect.dispatchEvent(new Event("change", { bubbles: true }));
      }

      kindButtons.forEach((kindButton) => {
        kindButton.classList.remove("selected");
      });

      button.classList.add("selected");
    });
  });

  const installButton = document.getElementById("pwa-install");
  let deferredInstallPrompt;

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    if (installButton) {
      installButton.hidden = false;
    }
  });

  installButton?.addEventListener("click", async () => {
    if (!deferredInstallPrompt) {
      return;
    }

    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    installButton.hidden = true;
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    if (installButton) {
      installButton.hidden = true;
    }
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/service-worker.js")
        .catch((error) => console.error("SmartFinX service worker failed:", error));
    });
  }
})();
