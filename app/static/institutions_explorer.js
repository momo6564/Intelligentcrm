(function () {
  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function distinctSorted(rows, key) {
    return [...new Set(rows.map((row) => String(row[key] || "").trim()).filter(Boolean))].sort((a, b) =>
      a.localeCompare(b)
    );
  }

  const US_FOCUS_BOUNDS = [
    [24.396308, -125.0],
    [49.384358, -66.93457],
  ];

  const US_MAX_BOUNDS = [
    [17.5, -171.0],
    [72.5, -52.0],
  ];

  window.createInstitutionExplorerStore = function createInstitutionExplorerStore(config) {
    return {
      ready: false,
      loading: false,
      detailLoading: false,
      error: "",
      mode: config.mode || "list",
      mapElementId: config.mapElementId || "institutions-map",
      querySync: config.querySync !== false,
      navigateMapClicks: !!config.navigateMapClicks,
      tablePageSize: Number(config.tablePageSize || 80),
      tableVisible: Number(config.tablePageSize || 80),
      currentTheme: config.initialTheme || "carto_light",
      selectedId: config.initialInstitutionId || null,
      selectedDetail: null,
      detailCache: {},
      searchTimer: null,
      allRows: [],
      filteredRows: [],
      options: { states: [], controls: [], levels: [] },
      filters: {
        search: config.initialFilters?.search || "",
        state: config.initialFilters?.state || "",
        control: config.initialFilters?.control || "",
        institution_level: config.initialFilters?.institution_level || "",
      },
      map: null,
      tileLayer: null,
      markerLayer: null,
      markerById: new Map(),
      highlightedId: null,
      renderer: null,
      themeOptions: [
        { value: "carto_light", label: "Light Map" },
        { value: "carto_dark", label: "Dark Map" },
        { value: "osm", label: "Street Map" },
        { value: "terrain", label: "Terrain" },
      ],
      tileThemes: {
        carto_light: {
          url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
          options: { attribution: "&copy; OpenStreetMap contributors &copy; CARTO", subdomains: "abcd" },
        },
        carto_dark: {
          url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
          options: { attribution: "&copy; OpenStreetMap contributors &copy; CARTO", subdomains: "abcd" },
        },
        osm: {
          url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
          options: { attribution: "&copy; OpenStreetMap contributors" },
        },
        terrain: {
          url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
          options: {
            attribution: "Map data &copy; OpenStreetMap contributors, SRTM | Map style &copy; OpenTopoMap",
          },
        },
      },

      init() {
        if (this.ready) return;
        this.ready = true;
        this.initMap();
        this.load();
      },

      initMap() {
        const mapNode = document.getElementById(this.mapElementId);
        if (!mapNode || typeof L === "undefined") return;
        this.renderer = L.canvas({ padding: 0.5 });
        this.map = L.map(this.mapElementId, {
          center: [39.8283, -98.5795],
          zoom: 4,
          minZoom: 3,
          maxZoom: 16,
          zoomControl: true,
          preferCanvas: true,
          maxBounds: US_MAX_BOUNDS,
          maxBoundsViscosity: 1.0,
        });
        this.markerLayer = L.layerGroup().addTo(this.map);
        this.setTile(this.currentTheme);
        this.focusUnitedStates();

        if (this.navigateMapClicks) {
          this.map.on("click", () => {
            window.location.href = this.mapPageUrl(this.selectedId);
          });
        }
      },

      setTile(themeName) {
        this.currentTheme = themeName;
        if (!this.map) return;
        const theme = this.tileThemes[themeName] || this.tileThemes.carto_light;
        if (this.tileLayer) this.map.removeLayer(this.tileLayer);
        this.tileLayer = L.tileLayer(theme.url, Object.assign({ maxZoom: 19, noWrap: true }, theme.options || {})).addTo(this.map);
      },

      focusUnitedStates() {
        if (!this.map) return;
        this.map.fitBounds(US_FOCUS_BOUNDS, { padding: [20, 20], maxZoom: 5 });
      },

      async load() {
        this.loading = true;
        this.error = "";
        try {
          const response = await fetch("/api/institutions?all=1&include_filters=1");
          const payload = await response.json();
          if (!response.ok || !payload.ok) {
            throw new Error(payload.error || "Could not load institutions");
          }
          this.allRows = (payload.results || [])
            .map((row) =>
              Object.assign({}, row, {
                latitude_num: Number(row.latitude),
                longitude_num: Number(row.longitude),
                _state_key: normalize(row.state),
                _control_key: normalize(row.control),
                _level_key: normalize(row.institution_level),
                _search_key: normalize([row.location_name, row.alias, row.city, row.state, row.website].join(" ")),
              })
            )
            .filter((row) => Number.isFinite(row.latitude_num) && Number.isFinite(row.longitude_num));
          this.buildOptions();
          this.applyFilters({ fit: true, preserveSelection: true });
        } catch (error) {
          this.error = error && error.message ? error.message : "Could not load institutions";
        } finally {
          this.loading = false;
        }
      },

      buildOptions() {
        this.options.states = distinctSorted(this.allRows, "state");
        this.options.controls = distinctSorted(this.allRows, "control");
        this.options.levels = distinctSorted(this.allRows, "institution_level");
      },

      debounceApply() {
        if (this.searchTimer) clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.applyFilters({ fit: false }), 180);
      },

      applyFilters(options) {
        const preserveSelection = options?.preserveSelection !== false;
        const searchKey = normalize(this.filters.search);
        const stateKey = normalize(this.filters.state);
        const controlKey = normalize(this.filters.control);
        const levelKey = normalize(this.filters.institution_level);

        this.filteredRows = this.allRows.filter((row) => {
          if (stateKey && row._state_key !== stateKey) return false;
          if (controlKey && row._control_key !== controlKey) return false;
          if (levelKey && row._level_key !== levelKey) return false;
          if (searchKey && !row._search_key.includes(searchKey)) return false;
          return true;
        });

        this.tableVisible = this.tablePageSize;
        this.renderMarkers();

        const selectionStillVisible =
          preserveSelection && this.selectedId && this.filteredRows.some((row) => Number(row.id) === Number(this.selectedId));
        if (selectionStillVisible) {
          if (Number(this.selectedDetail?.institution?.id) !== Number(this.selectedId)) {
            this.selectInstitution(this.selectedId, { pan: false, updateUrl: false });
          } else {
            this.highlightSelectedMarker();
          }
        } else if (this.filteredRows.length) {
          this.selectInstitution(this.filteredRows[0].id, { pan: false, updateUrl: false });
        } else {
          this.selectedId = null;
          this.selectedDetail = null;
          this.detailLoading = false;
          if (this.querySync) this.updateQueryString(null);
        }

        if (options?.fit) this.fitToFiltered();
      },

      renderMarkers() {
        if (!this.markerLayer) return;
        this.markerLayer.clearLayers();
        this.markerById.clear();
        this.highlightedId = null;
        this.filteredRows.forEach((row) => {
          const marker = L.circleMarker([row.latitude_num, row.longitude_num], {
            renderer: this.renderer,
            radius: this.markerRadius(row.students_total),
            color: "#ffffff",
            weight: 1.8,
            fillColor: this.controlColor(row.control),
            fillOpacity: 0.88,
          });
          marker.bindTooltip(
            `<div class="institution-tooltip-name">${this.escapeHtml(row.location_name || "-")}</div><div class="institution-tooltip-meta">${this.escapeHtml(row.city || "-")}, ${this.escapeHtml(row.state || "-")}</div>`,
            { direction: "top", offset: [0, -10], className: "institution-tooltip", opacity: 1 }
          );
          marker.on("click", () => {
            if (this.navigateMapClicks) {
              window.location.href = this.mapPageUrl(row.id);
            } else {
              this.selectInstitution(row.id);
            }
          });
          marker.addTo(this.markerLayer);
          this.markerById.set(Number(row.id), marker);
        });
        this.highlightSelectedMarker();
      },

      markerRadius(studentTotal) {
        const value = Number(studentTotal || 0);
        if (value >= 30000) return 10;
        if (value >= 15000) return 8;
        if (value >= 5000) return 6.5;
        if (value >= 1000) return 5;
        return 4;
      },

      controlColor(control) {
        const value = normalize(control);
        if (value.startsWith("public")) return "#3b82f6";
        if (value.includes("for-profit")) return "#d946ef";
        if (value.includes("not-for-profit")) return "#8b5cf6";
        return "#64748b";
      },

      highlightSelectedMarker() {
        if (this.highlightedId && this.markerById.has(Number(this.highlightedId))) {
          const previous = this.markerById.get(Number(this.highlightedId));
          const row = this.filteredRows.find((item) => Number(item.id) === Number(this.highlightedId));
          previous.setStyle({
            radius: this.markerRadius(row?.students_total),
            weight: 1.8,
            color: "#ffffff",
            fillOpacity: 0.88,
            fillColor: this.controlColor(row?.control),
          });
        }
        this.highlightedId = this.selectedId;
        if (this.selectedId && this.markerById.has(Number(this.selectedId))) {
          const current = this.markerById.get(Number(this.selectedId));
          const row = this.filteredRows.find((item) => Number(item.id) === Number(this.selectedId));
          current.setStyle({
            radius: this.markerRadius(row?.students_total) + 3,
            weight: 3,
            color: "#123d71",
            fillOpacity: 1,
            fillColor: this.controlColor(row?.control),
          });
          if (current.bringToFront) current.bringToFront();
        }
      },

      fitToFiltered() {
        if (!this.map || !this.filteredRows.length) return;
        const bounds = this.filteredRows.map((row) => [row.latitude_num, row.longitude_num]);
        this.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 6 });
        this.map.panInsideBounds(US_MAX_BOUNDS, { animate: false });
      },

      async selectInstitution(institutionId, options) {
        const id = Number(institutionId);
        if (!id) return;
        this.selectedId = id;
        this.highlightSelectedMarker();
        const row = this.selectedRow();
        if (row && this.map && options?.pan !== false) {
          this.map.flyTo([row.latitude_num, row.longitude_num], Math.max(this.map.getZoom(), 7), { duration: 0.55 });
        }
        if (this.querySync && options?.updateUrl !== false) this.updateQueryString(id);
        if (this.detailCache[id]) {
          this.selectedDetail = this.detailCache[id];
          this.detailLoading = false;
          return;
        }
        this.selectedDetail = null;
        this.detailLoading = true;
        try {
          const response = await fetch(`/api/institutions/${encodeURIComponent(id)}`);
          const payload = await response.json();
          if (!response.ok || !payload.ok) {
            throw new Error(payload.error || "Could not load institution details");
          }
          this.detailCache[id] = payload;
          if (Number(this.selectedId) === id) this.selectedDetail = payload;
        } catch (error) {
          this.error = error && error.message ? error.message : "Could not load institution details";
          if (window.appToast) window.appToast(this.error, "error");
        } finally {
          if (Number(this.selectedId) === id) this.detailLoading = false;
        }
      },

      updateQueryString(institutionId) {
        const url = new URL(window.location.href);
        if (institutionId) url.searchParams.set("institution_id", String(institutionId));
        else url.searchParams.delete("institution_id");
        window.history.replaceState({}, "", url.toString());
      },

      resetFilters() {
        this.filters = { search: "", state: "", control: "", institution_level: "" };
        this.applyFilters({ fit: true });
      },

      tableRows() {
        return this.filteredRows.slice(0, this.tableVisible);
      },

      hasMoreRows() {
        return this.tableVisible < this.filteredRows.length;
      },

      loadMoreRows() {
        this.tableVisible += this.tablePageSize;
      },

      mapPageUrl(institutionId) {
        const url = new URL("/institutions/detail", window.location.origin);
        if (institutionId) url.searchParams.set("institution_id", String(institutionId));
        if (this.filters.search) url.searchParams.set("q", this.filters.search);
        if (this.filters.state) url.searchParams.set("state", this.filters.state);
        if (this.filters.control) url.searchParams.set("control", this.filters.control);
        if (this.filters.institution_level) url.searchParams.set("institution_level", this.filters.institution_level);
        return url.pathname + url.search;
      },

      filterSummary() {
        if (this.loading) return "Loading institutions...";
        if (!this.filteredRows.length) return "No institutions match the current filters.";
        return `${this.number(this.filteredRows.length)} institutions across ${this.number(this.stateCount())} states`;
      },

      stateCount() {
        return new Set(this.filteredRows.map((row) => row.state).filter(Boolean)).size;
      },

      publicCount() {
        return this.filteredRows.filter((row) => normalize(row.control).startsWith("public")).length;
      },

      totalStudents() {
        return this.filteredRows.reduce((sum, row) => sum + Number(row.students_total || 0), 0);
      },

      averageStudents() {
        return this.filteredRows.length ? Math.round(this.totalStudents() / this.filteredRows.length) : 0;
      },

      topStates() {
        const counts = {};
        this.filteredRows.forEach((row) => {
          if (row.state) counts[row.state] = (counts[row.state] || 0) + 1;
        });
        return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
      },

      topStateLabel() {
        const top = this.topStates()[0];
        return top ? `${top[0]} - ${top[1]}` : "-";
      },

      selectedRow() {
        return this.allRows.find((row) => Number(row.id) === Number(this.selectedId)) || null;
      },

      selectedInstitution() {
        return this.selectedDetail?.institution || this.selectedRow() || null;
      },

      selectedChapters() {
        return this.selectedDetail?.chapters || [];
      },

      selectedInstitutionName() {
        return this.selectedInstitution()?.location_name || "No institution selected";
      },

      selectedInstitutionLocation() {
        const institution = this.selectedInstitution();
        if (!institution) return "Choose a campus from the table or map.";
        return `${institution.city || "-"}, ${institution.state || "-"}`;
      },

      selectedAddress() {
        const institution = this.selectedInstitution();
        if (!institution) return "-";
        const address = institution.address || institution.street || "";
        const locality = [institution.city, institution.state, institution.zip].filter(Boolean).join(" ");
        return [address, locality].filter(Boolean).join(", ") || "-";
      },

      selectedTags() {
        const institution = this.selectedInstitution();
        if (!institution) return [];
        const tags = [];
        if (institution.control) tags.push({ value: this.shortControl(institution.control), tone: this.controlPillClass(institution.control) });
        if (institution.institution_level) tags.push({ value: this.formatLevel(institution.institution_level), tone: "bg-amber-50 text-amber-700" });
        if (institution.degree_granting_status) tags.push({ value: institution.degree_granting_status, tone: "bg-violet-50 text-violet-700" });
        if (institution.locale) tags.push({ value: institution.locale, tone: "bg-slate-100 text-slate-600" });
        return tags;
      },

      selectedStatusLabel() {
        if (!this.selectedDetail) return "Not In CRM";
        if (this.selectedDetail.my_status === "served") return "Served";
        if (this.selectedDetail.my_status === "prospect") return "Prospect";
        return "Not In CRM";
      },

      selectedStatusClass() {
        if (!this.selectedDetail) return "bg-slate-100 text-slate-600";
        if (this.selectedDetail.my_status === "served") return "bg-emerald-100 text-emerald-700";
        if (this.selectedDetail.my_status === "prospect") return "bg-amber-100 text-amber-700";
        return "bg-slate-100 text-slate-600";
      },

      selectedWebsiteHref() {
        const website = this.selectedInstitution()?.website || "";
        if (!website) return "";
        return website.includes("://") ? website : `https://${website}`;
      },

      selectedProfileUrl() {
        const institution = this.selectedInstitution();
        return institution ? `/institutions/detail?institution_id=${encodeURIComponent(institution.id)}` : "/institutions";
      },

      selectedResearchUrl() {
        const institution = this.selectedInstitution();
        if (!institution) return "https://www.google.com";
        const query = `${institution.location_name || ""} approved vendors procurement site:.edu`;
        return `https://www.google.com/search?q=${encodeURIComponent(query)}`;
      },

      async markInstitution(action) {
        const institution = this.selectedInstitution();
        if (!institution) return;
        try {
          const response = await fetch("/api/m/crm/add-institution", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              institution_id: institution.id,
              institution_name: institution.location_name || "",
              city: institution.city || "",
              state: institution.state || "",
              action,
            }),
          });
          const payload = await response.json();
          if (!response.ok || !payload.ok) throw new Error(payload.error || "Failed to update CRM");
          if (this.selectedDetail) this.selectedDetail.my_status = action === "served" ? "served" : "prospect";
          if (window.appToast) {
            window.appToast(action === "served" ? "Institution added as served." : "Institution added as prospect.", "success");
          }
        } catch (error) {
          if (window.appToast) window.appToast(error.message || "Failed to update CRM", "error");
        }
      },

      controlPillClass(control) {
        const value = normalize(control);
        if (value.startsWith("public")) return "bg-blue-50 text-blue-700";
        if (value.includes("for-profit")) return "bg-fuchsia-50 text-fuchsia-700";
        if (value.includes("not-for-profit")) return "bg-violet-50 text-violet-700";
        return "bg-slate-100 text-slate-600";
      },

      shortControl(control) {
        const value = normalize(control);
        if (!value) return "";
        if (value.startsWith("public")) return "Public";
        if (value.includes("for-profit")) return "Private (For-profit)";
        if (value.includes("not-for-profit")) return "Private (Nonprofit)";
        return control;
      },

      formatLevel(level) {
        const value = normalize(level);
        if (value === "4 year") return "4-Year";
        if (value === "2 year") return "2-Year";
        if (value === "less than 2 year") return "<2-Year";
        return level || "";
      },

      formatPercent(value) {
        const number = Number(value);
        if (!Number.isFinite(number) || number === 0) return "-";
        return number > 1 ? `${number}%` : `${(number * 100).toFixed(1)}%`;
      },

      number(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return "-";
        return number.toLocaleString();
      },

      escapeHtml(value) {
        return String(value || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      },
    };
  };
})();
