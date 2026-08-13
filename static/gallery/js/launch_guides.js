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

  function initArtifactDeck() {
    const artifacts = Array.from(document.querySelectorAll(".launch-artifact[data-artifact]"));
    const tiles = Array.from(document.querySelectorAll(".launch-route[data-guide-slug]"));
    const count = document.getElementById("launch-match-count");
    const hint = document.getElementById("launch-board-hint");
    const clear = document.querySelector("[data-artifact-clear]");
    if (!artifacts.length || !tiles.length) return;

    const emptyCopy = (count && count.dataset.emptyCopy) || "All routes shown";
    const emptyHint = "Tap a card above — the matching destinations light up instantly.";
    const matchHint = "Lit-up cards match what you are holding. Open one for the source-backed steps.";
    const zeroHint = "No matching route in this filter — tap “All destinations” above to see them.";

    function currentArtifact() {
      return artifacts.find((el) => el.classList.contains("is-active"));
    }

    function apply(artifactValue) {
      const active = artifacts.find((el) => el.dataset.artifact === artifactValue) || null;
      artifacts.forEach((el) => {
        const on = el === active;
        el.classList.toggle("is-active", on);
        el.setAttribute("aria-pressed", String(on));
      });

      let matchCount = 0;
      if (active) {
        const slugs = (active.dataset.guides || "")
          .split(",")
          .map((slug) => slug.trim())
          .filter(Boolean);
        tiles.forEach((tile) => {
          const on = slugs.includes(tile.dataset.guideSlug);
          tile.classList.toggle("is-match", on);
          tile.classList.toggle("is-dimmed", !on);
          if (on) matchCount += 1;
        });
      } else {
        tiles.forEach((tile) => tile.classList.remove("is-match", "is-dimmed"));
      }

      if (count) {
        count.textContent = active ? `${matchCount} of ${tiles.length} routes match` : emptyCopy;
      }
      if (hint) {
        const icon = document.createElement("span");
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = active ? (matchCount ? "✓" : "⚠") : "👆";
        const copy = active ? (matchCount ? matchHint : zeroHint) : emptyHint;
        hint.replaceChildren(icon, document.createTextNode(` ${copy}`));
      }
      if (clear) clear.hidden = !active;
    }

    function updateUrl(artifactValue) {
      const params = new URLSearchParams(window.location.search);
      if (artifactValue) {
        params.set("artifact", artifactValue);
      } else {
        params.delete("artifact");
      }
      const category = params.get("category");
      if (category && category !== "all") {
        params.set("category", category);
      } else {
        params.delete("category");
      }
      const query = params.toString();
      const url = `${window.location.pathname}${query ? `?${query}` : ""}#deck`;
      try {
        window.history.replaceState(null, "", url);
      } catch (error) {
        // History updates are cosmetic; the deck still works without them.
      }
    }

    artifacts.forEach((artifact) => {
      artifact.addEventListener("click", (event) => {
        event.preventDefault();
        const next = artifact.classList.contains("is-active") ? "" : artifact.dataset.artifact;
        apply(next);
        updateUrl(next);
        if (next && window.innerWidth < 800) {
          const board = document.querySelector(".launch-board");
          if (board) board.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });

    if (clear) {
      clear.addEventListener("click", (event) => {
        event.preventDefault();
        apply("");
        updateUrl("");
      });
    }

    // Reconcile server-rendered state (e.g. after a category chip reload).
    const initial = currentArtifact();
    apply(initial ? initial.dataset.artifact : "");
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
    initArtifactDeck();
    initChecklist();
  });
})();
