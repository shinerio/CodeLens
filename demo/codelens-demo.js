document.addEventListener("DOMContentLoaded", () => {
  if (window.lucide) window.lucide.createIcons();

  document.querySelectorAll("[data-mobile-menu]").forEach((button) => {
    button.addEventListener("click", () => document.body.classList.toggle("nav-open"));
  });

  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => document.body.classList.remove("nav-open"));
  });

  document.querySelectorAll("[data-segmented]").forEach((group) => {
    group.querySelectorAll("button:not(:disabled)").forEach((button) => {
      button.addEventListener("click", () => {
        group.querySelectorAll("button").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        const target = group.getAttribute("data-target");
        if (target) {
          document.querySelectorAll(`[data-conditional="${target}"]`).forEach((item) => {
            item.hidden = item.getAttribute("data-value") !== button.dataset.value;
          });
        }
      });
    });
  });

  document.querySelectorAll("[data-tabs]").forEach((tabs) => {
    tabs.querySelectorAll("[data-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        const panelName = tab.getAttribute("data-tab");
        tabs.querySelectorAll("[data-tab]").forEach((item) => item.classList.remove("active"));
        tab.classList.add("active");
        const scope = tabs.closest("[data-tab-scope]") || document;
        scope.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.hidden = panel.getAttribute("data-panel") !== panelName;
        });
      });
    });
  });

  document.querySelectorAll("[data-filter-input]").forEach((input) => {
    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      const scope = input.closest("[data-filter-scope]") || document;
      scope.querySelectorAll("[data-filter-item]").forEach((item) => {
        item.hidden = !item.textContent.toLowerCase().includes(query);
      });
    });
  });

  document.querySelectorAll("[data-select-item]").forEach((item) => {
    item.addEventListener("click", () => {
      const scope = item.closest("[data-select-scope]") || document;
      scope.querySelectorAll("[data-select-item]").forEach((entry) => entry.classList.remove("active"));
      item.classList.add("active");
      const title = item.querySelector(".finding-item-title")?.textContent;
      const detailTitle = scope.querySelector("[data-detail-title]");
      if (title && detailTitle) detailTitle.textContent = title;
    });
  });

  document.querySelectorAll("[data-inspect]").forEach((button) => {
    button.addEventListener("click", () => {
      const original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = '<i data-lucide="loader-circle"></i> Inspecting';
      if (window.lucide) window.lucide.createIcons();
      window.setTimeout(() => {
        document.querySelectorAll("[data-inspection-result]").forEach((result) => {
          result.hidden = false;
        });
        document.querySelectorAll("[data-start-review]").forEach((start) => {
          start.disabled = false;
        });
        button.disabled = false;
        button.innerHTML = original;
        if (window.lucide) window.lucide.createIcons();
        showToast("Repository inspected. 18 review targets are ready.");
      }, 650);
    });
  });

  document.querySelectorAll("[data-start-review]").forEach((button) => {
    button.addEventListener("click", () => {
      button.disabled = true;
      button.innerHTML = '<i data-lucide="loader-circle"></i> Creating snapshot';
      if (window.lucide) window.lucide.createIcons();
      window.setTimeout(() => {
        window.location.href = "codelens-demo-review.html";
      }, 700);
    });
  });

  document.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", () => showToast("Settings saved locally for this demo."));
  });

  document.querySelectorAll("[data-toast]").forEach((button) => {
    button.addEventListener("click", () => showToast(button.getAttribute("data-toast")));
  });
});

function showToast(message) {
  let toast = document.querySelector(".toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.className = "toast";
    toast.innerHTML = '<i data-lucide="circle-check"></i><span></span>';
    document.body.appendChild(toast);
  }
  toast.querySelector("span").textContent = message;
  if (window.lucide) window.lucide.createIcons();
  toast.classList.add("show");
  window.clearTimeout(window.__codelensToastTimer);
  window.__codelensToastTimer = window.setTimeout(() => toast.classList.remove("show"), 2400);
}
