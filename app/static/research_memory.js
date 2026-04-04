(function () {
  function clean(value) {
    return String(value || "").trim();
  }

  function replaceTokens(template, context) {
    const normalized = {};
    Object.entries(context || {}).forEach(([key, value]) => {
      normalized[clean(key).toLowerCase()] = clean(value);
    });
    return clean(template).replace(/\{([a-z0-9_]+)\}/gi, (_, token) => normalized[clean(token).toLowerCase()] || "").replace(/\s+/g, " ").trim();
  }

  window.createResearchMemory = function createResearchMemory(config) {
    return {
      category: clean(config.category).toLowerCase(),
      title: clean(config.title) || "Research Memory",
      entityLabel: clean(config.entityLabel) || "record",
      placeholderHints: Array.isArray(config.placeholderHints) ? config.placeholderHints : [],
      slots: Array.from({ length: 5 }, (_, index) => ({
        slot_index: index + 1,
        label: "",
        prompt_text: "",
      })),
      loading: false,
      saving: false,

      init() {
        this.load();
      },

      entityData() {
        if (typeof config.getEntityData === "function") {
          try {
            return config.getEntityData() || {};
          } catch (_error) {
            return {};
          }
        }
        return config.entityData || {};
      },

      activeSlots() {
        return this.slots.filter((slot) => clean(slot.prompt_text));
      },

      renderedPrompt(slot) {
        return replaceTokens(slot.prompt_text, this.entityData());
      },

      slotUrl(slot) {
        const rendered = this.renderedPrompt(slot);
        return rendered ? `https://www.google.com/search?q=${encodeURIComponent(rendered)}` : "";
      },

      open(slot) {
        const url = this.slotUrl(slot);
        if (!url) {
          if (window.appToast) window.appToast("Add a prompt first.", "error");
          return;
        }
        window.open(url, "_blank", "noopener");
      },

      applySlots(prompts) {
        const source = Array.isArray(prompts) ? prompts : [];
        this.slots = Array.from({ length: 5 }, (_, index) => {
          const row = source[index] || {};
          return {
            slot_index: index + 1,
            label: clean(row.label),
            prompt_text: clean(row.prompt_text),
          };
        });
      },

      async load() {
        this.loading = true;
        try {
          const response = await fetch(`/api/research-prompts?category=${encodeURIComponent(this.category)}`);
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not load research prompts");
          this.applySlots(payload.prompts || []);
        } catch (error) {
          if (window.appToast) window.appToast(error.message || "Could not load research prompts", "error");
        } finally {
          this.loading = false;
        }
      },

      async save() {
        this.saving = true;
        try {
          const response = await fetch("/api/research-prompts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              category: this.category,
              prompts: this.slots.map((slot) => ({
                slot_index: slot.slot_index,
                label: slot.label,
                prompt_text: slot.prompt_text,
              })),
            }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not save research prompts");
          this.applySlots(payload.prompts || []);
          if (window.appToast) window.appToast("Research memory saved for your user.", "success");
        } catch (error) {
          if (window.appToast) window.appToast(error.message || "Could not save research prompts", "error");
        } finally {
          this.saving = false;
        }
      },

      async resetDefaults() {
        this.saving = true;
        try {
          const response = await fetch("/api/research-prompts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              category: this.category,
              reset_defaults: true,
            }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not reset research prompts");
          this.applySlots(payload.prompts || []);
          if (window.appToast) window.appToast("Research prompts restored to defaults.", "success");
        } catch (error) {
          if (window.appToast) window.appToast(error.message || "Could not reset research prompts", "error");
        } finally {
          this.saving = false;
        }
      },
    };
  };
})();
