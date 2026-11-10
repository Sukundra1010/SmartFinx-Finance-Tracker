(() => {
  if (!window.Chart) {
    return;
  }

  const readJson = (id) => {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : [];
  };

  const categoryPalette = [
    "#27a875",
    "#438fe4",
    "#e8499d",
    "#8661df",
    "#f08332",
    "#ee5663",
    "#e3a817",
    "#9a72c5",
  ];

  const centerLabelPlugin = {
    id: "centerLabel",
    afterDraw(chart, args, settings) {
      if (!settings?.text || chart.config.type !== "doughnut") {
        return;
      }

      const { ctx, chartArea } = chart;
      if (!chartArea) {
        return;
      }

      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = getComputedStyle(
        document.documentElement,
      ).getPropertyValue("--text").trim();
      ctx.font = "700 13px Manrope, sans-serif";

      const centerX = (chartArea.left + chartArea.right) / 2;
      const centerY = (chartArea.top + chartArea.bottom) / 2;
      ctx.fillText(settings.text, centerX, centerY);
      ctx.restore();
    },
  };

  const overviewCanvas = document.getElementById("overview-chart");
  if (overviewCanvas) {
    new Chart(overviewCanvas, {
      type: "bar",
      data: {
        labels: readJson("month-labels"),
        datasets: [
          {
            label: "Income",
            data: readJson("income-series"),
            backgroundColor: "#378765",
            borderRadius: 5,
          },
          {
            label: "Expenses",
            data: readJson("expense-series"),
            backgroundColor: "#e5aa9a",
            borderRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              boxWidth: 7,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
          },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(120,140,130,.12)" },
            border: { display: false },
          },
        },
      },
    });
  }

  const doughnutCharts = [
    ["category-chart", "category-labels", "category-series"],
    ["annual-category-chart", "annual-category-labels", "annual-category-values"],
  ];

  doughnutCharts.forEach(([canvasId, labelsId, valuesId]) => {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
      return;
    }

    const values = readJson(valuesId);
    const total = values.reduce((sum, value) => sum + value, 0);
    const formattedTotal = total.toLocaleString("en-IN", {
      maximumFractionDigits: 0,
    });

    new Chart(canvas, {
      type: "doughnut",
      plugins: [centerLabelPlugin],
      data: {
        labels: readJson(labelsId),
        datasets: [
          {
            data: values,
            backgroundColor: categoryPalette,
            borderWidth: 0,
            hoverOffset: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          centerLabel: {
            text: `Rs. ${formattedTotal}`,
          },
          legend: {
            display: false,
          },
        },
      },
    });
  });

  const paymentMethodCanvas = document.getElementById("payment-method-chart");
  if (paymentMethodCanvas) {
    new Chart(paymentMethodCanvas, {
      type: "doughnut",
      data: {
        labels: readJson("payment-method-labels"),
        datasets: [
          {
            data: readJson("payment-method-values"),
            backgroundColor: readJson("payment-method-colors"),
            borderWidth: 0,
            hoverOffset: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: {
          legend: {
            display: false,
          },
        },
      },
    });
  }

  const annualCanvas = document.getElementById("annual-chart");
  if (annualCanvas) {
    new Chart(annualCanvas, {
      type: "line",
      data: {
        labels: readJson("annual-labels"),
        datasets: [
          {
            label: "Income",
            data: readJson("annual-income"),
            borderColor: "#378765",
            backgroundColor: "rgba(55,135,101,.12)",
            fill: true,
            tension: 0.35,
          },
          {
            label: "Expenses",
            data: readJson("annual-expenses"),
            borderColor: "#d87965",
            backgroundColor: "rgba(216,121,101,.08)",
            fill: true,
            tension: 0.35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              usePointStyle: true,
              boxWidth: 7,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
          },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(120,140,130,.12)" },
            border: { display: false },
          },
        },
      },
    });
  }
})();
