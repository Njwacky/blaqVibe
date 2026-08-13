(() => {
  "use strict";

  const COPY_RESET_DELAY_MS = 1600;
  const copyResetTimers = new WeakMap();

  function copyWithFallback(text) {
    const previouslyFocused = document.activeElement;
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    area.style.pointerEvents = "none";
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, area.value.length);

    let copied = false;
    try {
      copied = document.execCommand("copy");
    } finally {
      area.remove();
      if (previouslyFocused instanceof HTMLElement) {
        previouslyFocused.focus({ preventScroll: true });
      }
    }
    return copied;
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (error) {
        // Browser permission policies can reject Clipboard API access. The
        // legacy selection path still works in some of those environments.
      }
    }
    return copyWithFallback(text);
  }

  function showCopyResult(button, copied) {
    const previousTimer = copyResetTimers.get(button);
    if (previousTimer) window.clearTimeout(previousTimer);

    const originalLabel = button.dataset.copyLabel || button.textContent;
    button.dataset.copyLabel = originalLabel;
    button.textContent = copied ? "Copied" : "Copy failed";
    button.dataset.copyStatus = copied ? "success" : "error";

    const timer = window.setTimeout(() => {
      button.textContent = originalLabel;
      delete button.dataset.copyStatus;
      copyResetTimers.delete(button);
    }, COPY_RESET_DELAY_MS);
    copyResetTimers.set(button, timer);
  }

  function initCopyButtons() {
    document.querySelectorAll("[data-copy-command]").forEach((button) => {
      button.addEventListener("click", async () => {
        const command = button.dataset.copyCommand || "";
        if (!command) {
          showCopyResult(button, false);
          return;
        }

        let copied = false;
        try {
          copied = await copyText(command);
        } catch (error) {
          console.error("The command could not be copied.", error);
        }
        showCopyResult(button, copied);
      });
    });
  }

  function initArtifactRouter() {
    const select = document.getElementById("launch-artifact");
    const result = document.getElementById("launch-recommendation");
    const linkElements = document.querySelectorAll("#launch-route-links [data-guide-link]");
    if (!select || !result || !linkElements.length) return;

    const guidesBySlug = new Map(
      Array.from(linkElements).map((link) => [
        link.dataset.guideLink,
        {
          url: link.getAttribute("href"),
          title: link.dataset.guideTitle,
          eyebrow: link.dataset.guideEyebrow,
        },
      ]),
    );

    select.addEventListener("change", () => {
      const selectedOption = select.selectedOptions[0];
      const slugs = (selectedOption?.dataset.guides || "")
        .split(",")
        .map((slug) => slug.trim())
        .filter(Boolean);
      const guides = slugs.map((slug) => guidesBySlug.get(slug)).filter(Boolean);

      if (!guides.length) {
        const empty = document.createElement("div");
        empty.className = "launch-recommendation__empty";
        const icon = document.createElement("span");
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "⌁";
        const message = document.createElement("p");
        message.textContent = "Select an artifact to see the safest next route.";
        empty.append(icon, message);
        result.replaceChildren(empty);
        return;
      }

      const wrap = document.createElement("div");
      wrap.className = "launch-recommendation__result";
      const label = document.createElement("p");
      label.textContent = "Recommended path";
      const links = document.createElement("div");
      links.className = "launch-recommendation__links";

      guides.forEach((guide) => {
        const anchor = document.createElement("a");
        anchor.href = guide.url;

        const icon = document.createElement("span");
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "→";
        const copy = document.createElement("div");
        const eyebrow = document.createElement("small");
        eyebrow.textContent = guide.eyebrow;
        const title = document.createElement("b");
        title.textContent = guide.title;
        copy.append(eyebrow, title);
        anchor.append(icon, copy);
        links.appendChild(anchor);
      });

      wrap.append(label, links);
      result.replaceChildren(wrap);
    });
  }

  function initChecklist() {
    const root = document.querySelector("[data-checklist-key]");
    if (!root) return;

    const storageKey = root.dataset.checklistKey;
    const boxes = Array.from(root.querySelectorAll(".js-launch-check"));
    const progress = root.querySelector("#launch-progress");
    const progressBar = root.querySelector("#launch-progress-bar");
    const progressLabel = root.querySelector("#launch-progress-label");
    let saved = [];

    try {
      const parsed = JSON.parse(localStorage.getItem(storageKey) || "[]");
      if (Array.isArray(parsed)) saved = parsed;
    } catch (error) {
      saved = [];
    }

    boxes.forEach((box) => {
      box.checked = saved.includes(box.value);
    });

    const paint = () => {
      const complete = boxes.filter((box) => box.checked).length;
      const percentage = boxes.length ? (complete / boxes.length) * 100 : 0;
      if (progress) progress.setAttribute("aria-valuenow", String(complete));
      if (progressBar) progressBar.style.width = `${percentage}%`;
      if (progressLabel) {
        progressLabel.textContent = `${complete} of ${boxes.length} complete`;
      }
      try {
        localStorage.setItem(
          storageKey,
          JSON.stringify(boxes.filter((box) => box.checked).map((box) => box.value)),
        );
      } catch (error) {
        // Storage can be disabled or full. The visible checklist still works.
      }
    };

    boxes.forEach((box) => box.addEventListener("change", paint));
    paint();
  }

  document.addEventListener("DOMContentLoaded", () => {
    initCopyButtons();
    initArtifactRouter();
    initChecklist();
  });
})();
