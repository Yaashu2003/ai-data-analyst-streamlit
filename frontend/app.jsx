const { useEffect, useMemo, useRef, useState } = React;

const API = "/api";

function formatCount(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString() : String(value || 0);
}

function asFloats(values) {
  return (values || []).map((value) => Number(value)).filter((value) => Number.isFinite(value));
}

function chartPlatform(chart) {
  return chart.dashboard_platform || chart.platform || "Dashboard";
}

function usableCharts(charts) {
  return (charts || []).filter((chart) => chart && chart.data_available !== false);
}

function trimSingleSeries(xValues, yValues, limit = 18) {
  const pairs = (xValues || []).map((x, index) => ({ x, y: Number((yValues || [])[index] || 0) }));
  if (pairs.length <= limit) return { x: xValues || [], y: yValues || [] };
  const trimmed = pairs
    .filter((item) => item.x !== null && item.x !== undefined && String(item.x).trim() !== "")
    .sort((a, b) => Math.abs(b.y) - Math.abs(a.y))
    .slice(0, limit);
  return {
    x: trimmed.map((item) => String(item.x)),
    y: trimmed.map((item) => item.y),
  };
}

function trimMultiSeries(xValues, series, limit = 16) {
  if (!xValues || xValues.length <= limit) return { x: xValues || [], series: series || [] };
  const totals = xValues.map((x, index) => {
    const total = (series || []).reduce((sum, item) => sum + Math.abs(Number((item.y || [])[index] || 0)), 0);
    return { x, index, total };
  });
  const keep = totals.sort((a, b) => b.total - a.total).slice(0, limit);
  return {
    x: keep.map((item) => String(item.x)),
    series: (series || []).map((item) => ({
      name: item.name || "Series",
      y: keep.map((kept) => Number((item.y || [])[kept.index] || 0)),
    })),
  };
}

function buildPlot(chart, compact = false) {
  const chartType = String(chart.chart_type || "").toLowerCase();
  let x = chart.x || [];
  let y = chart.y || [];
  const title = chart.title || "Chart";
  const dimension = chart.dimension || "Category";
  const measure = chart.measure_used || "Value";
  const colorway = ["#0f766e", "#2563eb", "#f97316", "#db2777", "#7c3aed", "#16a34a", "#ea580c", "#0891b2"];

  if (chartType.includes("card") && chart.card_value !== undefined) {
    return {
      data: [{
        type: "indicator",
        mode: "number",
        value: Number(chart.card_value) || 0,
        title: { text: title },
      }],
      layout: {
        height: compact ? 220 : 280,
        margin: { l: 24, r: 24, t: 70, b: 24 },
        paper_bgcolor: "#fff",
        font: { color: "#172033" },
      },
    };
  }

  const denseCategorical = x.length > 24 || x.some((label) => String(label).length > 18);
  let seriesPayload = chart.series || [];
  if (seriesPayload.length && denseCategorical && !chartType.includes("scatter")) {
    const trimmed = trimMultiSeries(x, seriesPayload);
    x = trimmed.x;
    seriesPayload = trimmed.series;
  } else if (!seriesPayload.length && denseCategorical && !chartType.includes("scatter") && !chartType.includes("line")) {
    const trimmed = trimSingleSeries(x, y);
    x = trimmed.x;
    y = trimmed.y;
  }

  let data = [];
  if (seriesPayload.length) {
    data = seriesPayload.map((item, index) => {
      const seriesX = item.x && item.x.length ? item.x : x;
      const seriesY = item.y || [];
      if (chartType.includes("scatter")) {
        const numericX = asFloats(seriesX);
        const numericY = asFloats(seriesY);
        return {
          type: "scatter",
          mode: "markers",
          name: item.name || `Series ${index + 1}`,
          x: numericX.length === numericY.length ? numericX : seriesX,
          y: numericY.length ? numericY : seriesY,
          marker: { size: compact ? 6 : 8, color: colorway[index % colorway.length], opacity: 0.78 },
        };
      }
      if (chartType.includes("line")) {
        return {
          type: "scatter",
          mode: "lines+markers",
          name: item.name || `Series ${index + 1}`,
          x: seriesX,
          y: seriesY,
          line: { width: 3, color: colorway[index % colorway.length] },
        };
      }
      if (chartType.includes("area")) {
        return {
          type: "scatter",
          mode: "lines",
          fill: index === 0 ? "tozeroy" : "tonexty",
          name: item.name || `Series ${index + 1}`,
          x: seriesX,
          y: seriesY,
        };
      }
      return {
        type: "bar",
        name: item.name || `Series ${index + 1}`,
        x,
        y: seriesY,
        marker: { color: colorway[index % colorway.length] },
      };
    });
  } else if (chartType.includes("scatter")) {
    data = [{
      type: "scatter",
      mode: "markers",
      x: asFloats(x).length === y.length ? asFloats(x) : x,
      y: asFloats(y),
      marker: { size: compact ? 6 : 8, color: "#2563eb", opacity: 0.78 },
    }];
  } else if (chartType.includes("line")) {
    data = [{ type: "scatter", mode: "lines+markers", x, y, line: { width: 3, color: "#0f766e" } }];
  } else if (chartType.includes("area")) {
    data = [{ type: "scatter", mode: "lines", fill: "tozeroy", x, y, line: { width: 3, color: "#0891b2" } }];
  } else if (chartType.includes("treemap")) {
    data = [{ type: "treemap", labels: x, parents: x.map(() => ""), values: y, textinfo: "label+value" }];
  } else if (chartType.includes("funnel")) {
    data = [{ type: "funnel", y: x, x: y }];
  } else if ((chartType.includes("pie") || chartType.includes("donut")) && x.length <= 8) {
    data = [{ type: "pie", labels: x, values: y, hole: chartType.includes("donut") ? 0.45 : 0 }];
  } else if (chartType.includes("histogram")) {
    data = [{ type: "histogram", x: asFloats(x), nbinsx: 20 }];
  } else if (chartType.includes("box")) {
    data = [{ type: "box", x, y, boxmean: true }];
  } else {
    const horizontal = x.length > 12 || x.some((label) => String(label).length > 14);
    data = [{
      type: "bar",
      x: horizontal ? y : x,
      y: horizontal ? x : y,
      orientation: horizontal ? "h" : "v",
      marker: { color: "#0f766e", line: { color: "rgba(15, 23, 42, 0.18)", width: 0.7 } },
    }];
  }

  return {
    data,
    layout: {
      title: { text: title, x: 0.02, xanchor: "left", font: { size: compact ? 13 : 16 } },
      height: compact ? 310 : 430,
      margin: { l: 72, r: 32, t: compact ? 58 : 76, b: 84 },
      template: "plotly_white",
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#fbfdff",
      colorway,
      legend: { orientation: "h", y: 1.08 },
      font: { color: "#172033", family: "Aptos, Segoe UI, sans-serif" },
      xaxis: { title: dimension, automargin: true, gridcolor: "rgba(15,118,110,0.08)" },
      yaxis: { title: measure, automargin: true, gridcolor: "rgba(15,118,110,0.08)" },
      barmode: "group",
    },
  };
}

function Plot({ chart, compact = false }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    if (!("IntersectionObserver" in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "260px" },
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!ref.current || !window.Plotly) return;
    if (!chart || chart.data_available === false) return;
    if (!visible) return;
    const plot = buildPlot(chart, compact);
    const render = () => {
      if (!ref.current || !window.Plotly) return;
      window.Plotly.react(ref.current, plot.data, plot.layout, {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
      });
    };
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(render, { timeout: 1200 });
    } else {
      window.setTimeout(render, 0);
    }
    return () => {
      if (ref.current && window.Plotly) window.Plotly.purge(ref.current);
    };
  }, [chart, compact, visible]);

  if (!chart || chart.data_available === false) {
    return <div className="empty-chart">Visual definition only. Exact source rows were not available.</div>;
  }

  return (
    <div className={compact ? "plot compact" : "plot"} ref={ref}>
      {!visible ? <div className="plot-placeholder">Chart will render when it scrolls into view.</div> : null}
    </div>
  );
}

function UploadPanel({ onJobCreated, job }) {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!files.length) return;
    setUploading(true);
    setError("");
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    try {
      const response = await fetch(`${API}/jobs`, { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Upload failed");
      onJobCreated(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="hero">
      <div className="hero-copy">
        <p className="eyebrow">Unified AI data analyst</p>
        <h1>Analyze dashboards and datasets without freezing the app.</h1>
        <p className="lead">
          Upload Tableau, Power BI, CSV, or Excel files. Processing runs as a background job,
          charts stay interactive, and the report/chat workspace opens as soon as results are ready.
        </p>
        <div className="hero-pills">
          <span>PBIX</span>
          <span>TWB/TWBX</span>
          <span>CSV/XLSX</span>
          <span>Mixed uploads</span>
          <span>Chart chat</span>
        </div>
      </div>
      <form onSubmit={submit} className="upload-card">
        <div className="upload-drop">
          <input
            type="file"
            multiple
            accept=".csv,.xlsx,.xls,.pbix,.twb,.twbx"
            onChange={(event) => setFiles(Array.from(event.target.files || []))}
          />
          <strong>{files.length ? `${files.length} file(s) selected` : "Drop dashboard or dataset files here"}</strong>
          <span>PBIX, TWB, TWBX, CSV, XLS, XLSX</span>
        </div>
        {files.length ? (
          <div className="file-list">
            {files.slice(0, 5).map((file) => <span key={file.name}>{file.name}</span>)}
          </div>
        ) : null}
        <button disabled={uploading || !files.length}>{uploading ? "Uploading..." : "Start analysis"}</button>
        {error ? <div className="error">{error}</div> : null}
        {job?.filenames?.length ? <p className="last-run">Current run: {job.filenames.join(", ")}</p> : null}
      </form>
    </section>
  );
}

function ProgressPanel({ job }) {
  const summary = job?.summary || {};
  const progress = Math.max(0, Math.min(100, Number(job?.progress || 0)));
  const status = job?.status || "waiting";
  const stages = [
    ["queued", "Queued"],
    ["processing", "Extracting"],
    ["processing", "Reporting"],
    ["complete", "Ready"],
  ];

  return (
    <section className="progress-panel">
      <div className="progress-header">
        <div>
          <span className="section-kicker">Run status</span>
          <strong>{job?.message || "Upload files to begin analysis."}</strong>
        </div>
        <span className={`status-badge ${status}`}>{status}</span>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="stage-row">
        {stages.map((stage, index) => (
          <span className={progress >= index * 33 || status === stage[0] ? "active" : ""} key={`${stage[1]}-${index}`}>
            {stage[1]}
          </span>
        ))}
      </div>
      <div className="metric-row">
        <Metric label="Charts with data" value={summary.chart_data_count} />
        <Metric label="Definitions" value={summary.metadata_visual_count} />
        <Metric label="Power BI" value={summary.pbix_report_count} />
        <Metric label="Tableau" value={summary.tableau_report_count} />
        <Metric label="Datasets" value={summary.dataset_report_count} />
        <Metric label="Visuals scanned" value={summary.visuals_seen} />
      </div>
    </section>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{formatCount(value)}</strong>
    </div>
  );
}

function ChartCard({ chart, compact = false }) {
  return (
    <article className={compact ? "chart-card compact-card" : "chart-card"}>
      <div className="chart-meta">
        <div>
          <h3>{chart.title || "Untitled visual"}</h3>
          <p>{chart.report || "Dashboard"} - {chartPlatform(chart)}</p>
        </div>
        <span>{chart.chart_type || "chart"}</span>
      </div>
      <Plot chart={chart} compact={compact} />
    </article>
  );
}

function ChartsView({ charts, loading = false }) {
  const [visibleCount, setVisibleCount] = useState(6);
  const visibleCharts = charts || [];
  useEffect(() => setVisibleCount(6), [visibleCharts.length]);
  if (loading) return <div className="empty-state">Loading chart workspace...</div>;
  if (!visibleCharts.length) return <div className="empty-state">Open this tab after a completed run to load interactive charts.</div>;
  const visibleSlice = visibleCharts.slice(0, visibleCount);
  return (
    <>
      <div className="chart-grid">
        {visibleSlice.map((chart, index) => (
          <ChartCard chart={chart} key={`${chart.report}-${chart.title}-${index}`} />
        ))}
      </div>
      {visibleCount < visibleCharts.length ? (
        <div className="load-more-row">
          <button onClick={() => setVisibleCount((count) => Math.min(count + 6, visibleCharts.length))}>
            Load more charts ({visibleCharts.length - visibleCount} remaining)
          </button>
        </div>
      ) : null}
    </>
  );
}

function renderInline(text) {
  const parts = String(text || "").split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

function ReportText({ text }) {
  const lines = String(text || "").split(/\r?\n/);
  return (
    <div className="report-flow">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div className="report-space" key={index} />;
        if (/^-{3,}$/.test(trimmed) || /^=+$/.test(trimmed)) return null;
        const heading = trimmed.match(/^(#{1,5})\s+(.*)$/);
        if (heading) {
          const level = Math.min(4, heading[1].length + 1);
          return React.createElement(`h${level}`, { key: index }, renderInline(heading[2]));
        }
        const insight = trimmed.match(/^Insight\s*#?\d*:?/i);
        if (insight) return <h3 key={index}>{renderInline(trimmed)}</h3>;
        const bullet = trimmed.match(/^[-*]\s+(.*)$/);
        if (bullet) {
          return (
            <div className="report-bullet" key={index}>
              <span />
              <p>{renderInline(bullet[1])}</p>
            </div>
          );
        }
        const numbered = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (numbered) {
          return (
            <div className="report-number" key={index}>
              <span>{numbered[1]}</span>
              <p>{renderInline(numbered[2])}</p>
            </div>
          );
        }
        return <p className="report-paragraph" key={index}>{renderInline(trimmed)}</p>;
      })}
    </div>
  );
}

function ReportFrame({ src, onSelection, onSelectionContext }) {
  const frameRef = useRef(null);

  useEffect(() => {
    const handleMessage = (event) => {
      if (event.origin !== window.location.origin) return;
      const payload = event.data || {};
      if (!payload.text) return;
      if (payload.type === "report-selection" || payload.type === "report-selection-context") {
        onSelection(payload.text);
      }
      if (payload.type === "report-selection-context") {
        onSelectionContext(payload.text);
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [onSelection, onSelectionContext]);

  function bindSelectionEvents() {
    const frame = frameRef.current;
    if (!frame || !frame.contentDocument) return;
    const doc = frame.contentDocument;
    if (doc.documentElement.dataset.selectionBridge === "true") return;
    doc.documentElement.dataset.selectionBridge = "true";

    const readSelection = (event, openDialog = false) => {
      const selected = String(doc.getSelection ? doc.getSelection().toString() : "").trim();
      if (!selected) return;
      onSelection(selected);
      if (openDialog) {
        event.preventDefault();
        onSelectionContext(selected);
      }
    };

    doc.addEventListener("mouseup", (event) => readSelection(event, false));
    doc.addEventListener("contextmenu", (event) => readSelection(event, true));
  }

  return (
    <iframe
      ref={frameRef}
      className="streamlit-report-frame"
      title="Interactive analysis report"
      src={src}
      onLoad={bindSelectionEvents}
    />
  );
}

function ReportView({ job, report, summary, onSelection, onSelectionContext }) {
  const reportHtmlUrl = job?.status === "complete" && job?.job_id
    ? (job.report_html_url || `/api/jobs/${job.job_id}/report-html?v=${job.updated_at || ""}`)
    : "";
  return (
    <section className="report-layout">
      <aside className="report-side">
        <span className="section-kicker">Report snapshot</span>
        <h2>Evidence-led analytics report</h2>
        <Metric label="Charts with data" value={summary?.chart_data_count} />
        <Metric label="Layout definitions" value={summary?.metadata_visual_count} />
        <Metric label="Dashboards processed" value={summary?.reports_processed} />
        <div className="report-side-note">
          Select text in the report, then open Feedback to regenerate just the wording you care about.
        </div>
      </aside>
      <div className="report-main">
        {reportHtmlUrl ? (
          <ReportFrame src={reportHtmlUrl} onSelection={onSelection} onSelectionContext={onSelectionContext} />
        ) : (
          <div className="report-paper" onMouseUp={() => onSelection(window.getSelection().toString())}>
            <ReportText text={report || "The report will appear here when analysis completes."} />
          </div>
        )}
      </div>
    </section>
  );
}

function ChatPanel({ job }) {
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);

  async function send() {
    const text = query.trim();
    if (!text || !job?.job_id) return;
    setMessages((items) => [{ role: "user", text }, ...items]);
    setQuery("");
    setLoading(true);
    try {
      const response = await fetch(`${API}/jobs/${job.job_id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: text }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Chat failed");
      setMessages((items) => [{ role: "assistant", payload: payload.response }, ...items]);
    } catch (err) {
      setMessages((items) => [{ role: "assistant", payload: { text: err.message } }, ...items]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="chat-layout">
      <div className="chat-input">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Ask about drivers, risks, cross-dashboard comparisons, or chart evidence..."
          onKeyDown={(event) => {
            if (event.key === "Enter") send();
          }}
        />
        <button onClick={send} disabled={loading || !query.trim()}>{loading ? "Thinking..." : "Send"}</button>
      </div>
      <div className="chat-thread">
        {messages.map((message, index) => (
          <div className={`message ${message.role}`} key={index}>
            {message.role === "user" ? <p>{message.text}</p> : <AssistantMessage payload={message.payload} />}
          </div>
        ))}
        {!messages.length ? (
          <div className="prompt-grid">
            {[
              "Compare the uploaded dashboards and name the strongest performer.",
              "What is the biggest risk, and which chart supports it?",
              "Create one comparison chart from the most comparable metric.",
            ].map((prompt) => (
              <button key={prompt} onClick={() => setQuery(prompt)}>{prompt}</button>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function AssistantMessage({ payload }) {
  if (!payload || typeof payload !== "object") return <p>{String(payload || "")}</p>;
  const chart = payload.response_type === "new_chart" ? payload : payload.chart;
  const sections = [
    ["overall_performance_summary", "Summary"],
    ["key_insights_and_drivers", "Key insights"],
    ["risks_and_issues", "Risks"],
    ["data_driven_recommendations", "Recommendations"],
    ["insights", "Supporting insights"],
  ];
  return (
    <div>
      {payload.text || payload.answer || payload.explanation ? <p>{payload.text || payload.answer || payload.explanation}</p> : null}
      {sections.map(([key, label]) => {
        const value = payload[key];
        if (!value || (Array.isArray(value) && !value.length)) return null;
        return (
          <div className="analysis-section" key={key}>
            <strong>{label}</strong>
            {Array.isArray(value) ? <ul>{value.map((item, i) => <li key={i}>{String(item)}</li>)}</ul> : <p>{String(value)}</p>}
          </div>
        );
      })}
      {chart && chart.x ? <div className="chat-chart"><Plot chart={chart} compact /></div> : null}
    </div>
  );
}

function FloatingChat({ job }) {
  const [open, setOpen] = useState(false);
  const ready = job?.status === "complete";
  return (
    <>
      <button className="floating-chat-button" onClick={() => setOpen(true)} disabled={!ready}>
        Chat
      </button>
      {open ? (
        <div className="floating-chat">
          <div className="floating-chat-header">
            <div>
              <span className="section-kicker">Dashboard assistant</span>
              <strong>Ask while reading the report</strong>
            </div>
            <button onClick={() => setOpen(false)}>Close</button>
          </div>
          <ChatPanel job={job} />
        </div>
      ) : null}
    </>
  );
}

function SelectionFeedbackDialog({ selection, onClose, onAddFeedback, onOpenFeedback }) {
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    setFeedback("");
  }, [selection?.text]);

  if (!selection?.open) return null;

  function saveFeedback() {
    if (!selection.text || !feedback.trim()) return;
    onAddFeedback({ text: selection.text, feedback: feedback.trim() });
    onClose();
  }

  return (
    <div className="selection-dialog-backdrop" onMouseDown={onClose}>
      <div className="selection-dialog" onMouseDown={(event) => event.stopPropagation()}>
        <span className="section-kicker">Human feedback</span>
        <h3>Refine selected report text</h3>
        <blockquote>{selection.text}</blockquote>
        <label>What should change?</label>
        <textarea
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
          placeholder="Example: make this more concise, mention the exact chart, or focus on pricing instead of inventory."
        />
        <div className="dialog-actions">
          <button onClick={onClose}>Cancel</button>
          <button onClick={saveFeedback} disabled={!feedback.trim()}>Add feedback</button>
          <button
            className="primary"
            onClick={() => {
              if (feedback.trim()) onAddFeedback({ text: selection.text, feedback: feedback.trim() });
              onOpenFeedback();
              onClose();
            }}
          >
            Open feedback editor
          </button>
        </div>
      </div>
    </div>
  );
}

function FeedbackView({ job, report, selectedText, sentenceFeedbacks, setSentenceFeedbacks, onReportUpdated, onReportHtmlUpdated }) {
  const [editedText, setEditedText] = useState(report || "");
  const [generalFeedback, setGeneralFeedback] = useState("");
  const [currentText, setCurrentText] = useState(selectedText || "");
  const [currentFeedback, setCurrentFeedback] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => setEditedText(report || ""), [report]);
  useEffect(() => {
    if (selectedText) setCurrentText(selectedText);
  }, [selectedText]);

  function addSentenceFeedback() {
    if (!currentText.trim() || !currentFeedback.trim()) return;
    setSentenceFeedbacks((existing) => [{ text: currentText.trim(), feedback: currentFeedback.trim() }, ...existing]);
    setCurrentFeedback("");
  }

  async function regenerate() {
    if (!job?.job_id) return;
    setLoading(true);
    try {
      const response = await fetch(`${API}/jobs/${job.job_id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          edited_text: editedText,
          general_feedback: generalFeedback,
          sentence_feedbacks: sentenceFeedbacks,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Regeneration failed");
      onReportUpdated(payload.report_text);
      onReportHtmlUpdated(`/api/jobs/${job.job_id}/report-html?v=${Date.now()}`);
      setEditedText(payload.report_text || "");
      setGeneralFeedback("");
      setSentenceFeedbacks([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="feedback-layout">
      <div className="feedback-editor">
        <label>Editable report draft</label>
        <textarea value={editedText} onChange={(event) => setEditedText(event.target.value)} />
      </div>
      <div className="feedback-panel">
        <label>Selected text</label>
        <textarea className="small" value={currentText} onChange={(event) => setCurrentText(event.target.value)} />
        <label>Feedback for selected text</label>
        <textarea className="small" value={currentFeedback} onChange={(event) => setCurrentFeedback(event.target.value)} />
        <button onClick={addSentenceFeedback}>Add sentence feedback</button>
        <label>Overall guidance</label>
        <textarea className="small" value={generalFeedback} onChange={(event) => setGeneralFeedback(event.target.value)} />
        <button className="primary" onClick={regenerate} disabled={loading}>{loading ? "Regenerating..." : "Regenerate with feedback"}</button>
        <div className="queued-feedback">
          {sentenceFeedbacks.map((item, index) => (
            <div key={index}>
              <strong>{item.text.slice(0, 80)}</strong>
              <p>{item.feedback}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function App() {
  const [job, setJob] = useState(null);
  const [charts, setCharts] = useState([]);
  const [chartsLoaded, setChartsLoaded] = useState(false);
  const [chartsLoading, setChartsLoading] = useState(false);
  const [report, setReport] = useState("");
  const [reportHtmlUrl, setReportHtmlUrl] = useState("");
  const [tab, setTab] = useState("report");
  const [selectedText, setSelectedText] = useState("");
  const [selectionDialog, setSelectionDialog] = useState({ open: false, text: "" });
  const [sentenceFeedbacks, setSentenceFeedbacks] = useState([]);

  async function loadJobReport(jobId) {
    const reportResponse = await fetch(`${API}/jobs/${jobId}/report`);
    const reportPayload = await reportResponse.json();
    setReport(reportPayload.report_text || "");
    setReportHtmlUrl(`${reportPayload.report_html_url || `${API}/jobs/${jobId}/report-html`}?v=${Date.now()}`);
  }

  async function loadJobCharts(jobId) {
    if (!jobId || chartsLoading || chartsLoaded) return;
    setChartsLoading(true);
    try {
      const chartsResponse = await fetch(`${API}/jobs/${jobId}/charts`);
      const chartsPayload = await chartsResponse.json();
      setCharts(chartsPayload.charts || []);
      setChartsLoaded(true);
    } finally {
      setChartsLoading(false);
    }
  }

  useEffect(() => {
    async function loadLatestJob() {
      try {
        const response = await fetch(`${API}/jobs`);
        const jobs = await response.json();
        const latest = Array.isArray(jobs) ? jobs.find((item) => item.status === "complete") || jobs[0] : null;
        if (latest?.job_id) {
          setJob(latest);
          if (latest.status === "complete") await loadJobReport(latest.job_id);
        }
      } catch (error) {
        console.warn(error);
      }
    }
    loadLatestJob();
  }, []);

  useEffect(() => {
    if (!job?.job_id || job.status === "complete" || job.status === "failed") return;
    const timer = setInterval(async () => {
      const response = await fetch(`${API}/jobs/${job.job_id}`);
      const payload = await response.json();
      setJob(payload);
      if (payload.status === "complete") await loadJobReport(job.job_id);
    }, 1200);
    return () => clearInterval(timer);
  }, [job]);

  useEffect(() => {
    if (tab === "charts" && job?.status === "complete") {
      loadJobCharts(job.job_id);
    }
  }, [tab, job?.job_id, job?.status]);

  const tabs = useMemo(() => [
    ["report", "Report"],
    ["charts", "Charts"],
    ["chat", "Chart chat"],
    ["feedback", "Feedback"],
  ], []);

  return (
    <main>
      <UploadPanel
        job={job}
        onJobCreated={(created) => {
          setJob(created);
          setCharts([]);
          setChartsLoaded(false);
          setReportHtmlUrl("");
          setReport("");
          setSelectedText("");
          setSentenceFeedbacks([]);
          setTab("report");
        }}
      />
      <ProgressPanel job={job} />
      <nav className="tabs">
        {tabs.map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>
      {tab === "report" ? (
        <ReportView
          job={job ? { ...job, report_html_url: reportHtmlUrl || job.report_html_url } : null}
          report={report}
          summary={job?.summary}
          onSelection={setSelectedText}
          onSelectionContext={(text) => setSelectionDialog({ open: true, text })}
        />
      ) : null}
      {tab === "charts" ? <ChartsView charts={charts} loading={chartsLoading} /> : null}
      {tab === "chat" ? <ChatPanel job={job} /> : null}
      {tab === "feedback" ? (
        <FeedbackView
          job={job}
          report={report}
          selectedText={selectedText}
          sentenceFeedbacks={sentenceFeedbacks}
          setSentenceFeedbacks={setSentenceFeedbacks}
          onReportUpdated={setReport}
          onReportHtmlUpdated={setReportHtmlUrl}
        />
      ) : null}
      <SelectionFeedbackDialog
        selection={selectionDialog}
        onClose={() => setSelectionDialog({ open: false, text: "" })}
        onAddFeedback={(item) => setSentenceFeedbacks((existing) => [item, ...existing])}
        onOpenFeedback={() => setTab("feedback")}
      />
      <FloatingChat job={job} />
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
