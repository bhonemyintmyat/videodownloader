import { useEffect, useState } from "react";

const TERMINAL_STATUSES = new Set(["completed", "partial", "failed"]);

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {})
    }
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ?? "The request could not be completed.";
    throw new Error(message);
  }

  return body;
}

function StatusPill({ status }) {
  return <span className={`status-pill status-${status}`}>{status}</span>;
}

function JobResult({ job }) {
  if (!job) {
    return null;
  }

  const completed = job.items.filter((item) => item.status === "completed").length;

  return (
    <section className="panel job-panel" aria-live="polite">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Batch job</p>
          <h2>Download queue</h2>
        </div>
        <StatusPill status={job.status} />
      </div>

      <div className="job-summary">
        <span>{completed} of {job.items.length} ready</span>
        <code>{job.id}</code>
      </div>

      <ul className="job-items">
        {job.items.map((item, index) => (
          <li className="job-item" key={`${item.url}-${index}`}>
            <div className="job-item-main">
              <span className="job-index">{String(index + 1).padStart(2, "0")}</span>
              <div className="job-item-copy">
                <p className="job-url">{item.url}</p>
                <div className="job-meta">
                  <StatusPill status={item.status} />
                  {item.cache_hit && <span className="cache-badge">cached</span>}
                  {item.media?.title && <span>{item.media.title}</span>}
                </div>
                {item.error && (
                  <p className="job-error">
                    {item.error.message} <code>{item.error.code}</code>
                  </p>
                )}
              </div>
              {item.delivery_url && (
                <a
                  className="download-link"
                  href={item.delivery_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open media
                </a>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function App() {
  const [url, setUrl] = useState("");
  const [validation, setValidation] = useState(null);
  const [validationError, setValidationError] = useState("");
  const [validating, setValidating] = useState(false);

  const [batchText, setBatchText] = useState("");
  const [batchError, setBatchError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [job, setJob] = useState(null);
  const [jobError, setJobError] = useState("");

  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!jobId || TERMINAL_STATUSES.has(jobStatus)) {
      return undefined;
    }

    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const nextJob = await request(`/api/downloads/${jobId}`);
        if (!cancelled) {
          setJob(nextJob);
        }
      } catch (error) {
        if (!cancelled) {
          setJobError(error.message);
        }
      }
    }, 1000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, jobStatus]);

  async function handleValidate(event) {
    event.preventDefault();
    setValidating(true);
    setValidationError("");
    setValidation(null);

    try {
      const result = await request("/api/validate", {
        method: "POST",
        body: JSON.stringify({ url })
      });
      setValidation(result);
    } catch (error) {
      setValidationError(error.message);
    } finally {
      setValidating(false);
    }
  }

  async function queueUrls(urls) {
    setSubmitting(true);
    setJobError("");
    setJob(null);

    try {
      const queued = await request("/api/downloads", {
        method: "POST",
        body: JSON.stringify({ urls })
      });
      setJob(queued);
    } catch (error) {
      setJobError(error.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleBatchSubmit(event) {
    event.preventDefault();
    setBatchError("");

    const urls = batchText
      .split(/\r?\n/)
      .map((entry) => entry.trim())
      .filter(Boolean);

    if (urls.length === 0) {
      setBatchError("Add at least one URL.");
      return;
    }

    await queueUrls(urls);
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <div className="brand">
          <span className="brand-mark">VS</span>
          <span>VideoSaver</span>
        </div>
        <p className="eyebrow">X video workflow</p>
        <h1>Validate a link. Queue a batch. Deliver with cache.</h1>
        <p className="hero-copy">
          URL parsing and provider detection are enforced on the server. Authorized
          media results are cached and delivered through your configured CDN.
        </p>
      </header>

      <div className="layout">
        <div className="column">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Single URL</p>
                <h2>Check a video link</h2>
              </div>
            </div>

            <form className="stack" onSubmit={handleValidate}>
              <label className="field">
                <span>X or Twitter URL</span>
                <input
                  type="url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://x.com/user/status/123456789"
                  autoComplete="off"
                  spellCheck="false"
                  required
                />
              </label>
              <button className="primary-button" type="submit" disabled={validating}>
                {validating ? "Checking..." : "Validate URL"}
              </button>
            </form>

            {validationError && <p className="alert">{validationError}</p>}

            {validation && (
              <div className={`result-card ${validation.supported ? "is-supported" : ""}`}>
                <div className="result-heading">
                  <span className={validation.supported ? "signal-good" : "signal-muted"}>
                    {validation.supported ? "Supported" : "Not supported"}
                  </span>
                  {validation.provider && (
                    <span className="provider-chip">{validation.provider.name}</span>
                  )}
                </div>
                <p className="result-message">{validation.message}</p>
                {validation.normalized_url && (
                  <code className="normalized-url">{validation.normalized_url}</code>
                )}
                {validation.content_id && (
                  <p className="content-id">Status ID: {validation.content_id}</p>
                )}
                {validation.supported && (
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => queueUrls([validation.normalized_url])}
                    disabled={submitting}
                  >
                    Queue this video
                  </button>
                )}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Batch</p>
                <h2>Queue multiple links</h2>
              </div>
              <span className="hint">One URL per line</span>
            </div>

            <form className="stack" onSubmit={handleBatchSubmit}>
              <label className="field">
                <span>Video URLs</span>
                <textarea
                  rows={7}
                  value={batchText}
                  onChange={(event) => setBatchText(event.target.value)}
                  placeholder={
                    "https://x.com/creator/status/123\nhttps://x.com/creator/status/456"
                  }
                  spellCheck="false"
                />
              </label>
              <button className="primary-button" type="submit" disabled={submitting}>
                {submitting ? "Queueing..." : "Queue batch"}
              </button>
            </form>

            {batchError && <p className="alert">{batchError}</p>}
          </section>
        </div>

        <div className="column">
          {jobError && <p className="alert">{jobError}</p>}
          <JobResult job={job} />

          <section className="panel note-panel">
            <p className="eyebrow">Delivery controls</p>
            <h2>Built for scale</h2>
            <ul className="feature-list">
              <li>
                <strong>Cache</strong>
                <span>Resolved media is reused by status ID until its TTL expires.</span>
              </li>
              <li>
                <strong>Queue</strong>
                <span>Batch jobs are processed inline and stored in shared state for polling.</span>
              </li>
              <li>
                <strong>Rate limit</strong>
                <span>Per-client token buckets return 429 with Retry-After.</span>
              </li>
              <li>
                <strong>CDN</strong>
                <span>Set CDN_BASE_URL to redirect delivery through a global edge.</span>
              </li>
            </ul>
            <p className="compliance-note">
              Media extraction uses the configured MEDIA_RESOLVER. The default adapter is
              unauthenticated yt-dlp. Download only content you are authorized to access.
            </p>
          </section>
        </div>
      </div>
    </main>
  );
}
