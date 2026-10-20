import { useEffect, useState } from "react";

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

function readRoute() {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash === "/download" || hash.startsWith("/download?")) {
    const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
    return {
      name: "download",
      url: new URLSearchParams(query).get("url") ?? ""
    };
  }
  return { name: "home", url: "" };
}

function navigate(path) {
  window.location.hash = path;
}

function formatDuration(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
    return null;
  }
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function downloadUrl(details, format) {
  const query = new URLSearchParams({
    format: format.id,
    url: details.source_url
  });
  return `/api/media/${encodeURIComponent(details.provider)}/${encodeURIComponent(details.id)}/download?${query}`;
}

const RESOLUTION_TIERS = [1080, 720, 360, 240, 144];

function containerLabel(contentType) {
  if (contentType === "video/mp4") {
    return "MP4";
  }
  return (contentType.split("/")[1] || "video").toUpperCase();
}

function resolutionTiers(formats) {
  const available = formats.filter(
    (format) => Number.isFinite(format.height) && format.height > 0
  );
  if (available.length === 0) {
    return { tiers: [], maxHeight: null };
  }

  const sorted = available.sort((left, right) => right.height - left.height);
  const maxHeight = sorted[0].height;
  const tiers = RESOLUTION_TIERS.map((target) => {
    const source =
      sorted.find((format) => format.height <= target) ?? sorted[sorted.length - 1];
    return { target, source };
  });

  return { tiers, maxHeight };
}

function DownloadPage({ sourceUrl }) {
  const [details, setDetails] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!sourceUrl) {
      setDetails(null);
      setError("No video URL was provided.");
      setLoading(false);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError("");
    setDetails(null);

    request("/api/media/details", {
      method: "POST",
      body: JSON.stringify({ url: sourceUrl })
    })
      .then((result) => {
        if (!cancelled) {
          setDetails(result);
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sourceUrl, attempt]);

  const duration = formatDuration(details?.duration_seconds);
  const { tiers, maxHeight } = resolutionTiers(details?.formats ?? []);

  return (
    <main className="app-shell">
      <header className="detail-header">
        <div className="brand">
          <span className="brand-mark">VS</span>
          <span>VideoSaver</span>
        </div>
        <button className="text-button" type="button" onClick={() => navigate("/")}>
          Back to validator
        </button>
      </header>

      <section className="panel download-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Video found</p>
            <h1>Choose a resolution</h1>
          </div>
          {details && <span className="provider-chip">{details.provider_name}</span>}
        </div>

        {loading && (
          <div className="detail-state" aria-live="polite">
            <span className="loading-dot" />
            Fetching video details...
          </div>
        )}

        {error && (
          <div className="detail-state is-error" role="alert">
            <p>{error}</p>
            <div className="detail-state-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={() => setAttempt((value) => value + 1)}
              >
                Try again
              </button>
              <button className="text-button" type="button" onClick={() => navigate("/")}>
                Validate another link
              </button>
            </div>
          </div>
        )}

        {details && (
          <div className="download-layout">
            <div className="thumbnail-column">
              <div className="thumbnail-frame">
                <span className="thumbnail-fallback">VS</span>
                {details.thumbnail_url && (
                  <img
                    src={details.thumbnail_url}
                    alt=""
                    loading="lazy"
                    referrerPolicy="no-referrer"
                    onError={(event) => {
                      event.currentTarget.style.display = "none";
                    }}
                  />
                )}
              </div>

              <h2 className="video-title">{details.title || "X video"}</h2>
              <p className="video-meta">
                {details.provider_name}
                {duration ? ` · ${duration}` : ""}
              </p>
              <a
                className="source-link"
                href={details.source_url}
                target="_blank"
                rel="noreferrer"
              >
                View original post
              </a>
            </div>

            <div className="format-column">
              <p className="eyebrow">Download options</p>
              {tiers.length > 0 ? (
                <>
                  <ul className="format-list">
                    {tiers.map(({ target, source }) => {
                      const exact = source.height === target;
                      return (
                        <li key={target}>
                          <a
                            className={`format-option ${exact ? "" : "is-mapped"}`}
                            href={downloadUrl(details, source)}
                            download
                          >
                            <span className="format-copy">
                              <span className="format-quality">{target}P</span>
                              <span className="format-detail">
                                {exact ? "Native" : "Source"} {source.height}P ·{" "}
                                {containerLabel(source.content_type)}
                              </span>
                            </span>
                            <span className="format-action">Download</span>
                          </a>
                        </li>
                      );
                    })}
                  </ul>
                  {maxHeight && (
                    <p className="resolution-note">
                      X source max: {maxHeight}P. Tiers without a native match use
                      the closest source.
                    </p>
                  )}
                </>
              ) : (
                <p className="alert">No downloadable resolutions were found.</p>
              )}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

function HomePage() {
  const [url, setUrl] = useState("");
  const [validation, setValidation] = useState(null);
  const [validationError, setValidationError] = useState("");
  const [validating, setValidating] = useState(false);

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

  function handleDownload() {
    if (validation?.normalized_url) {
      navigate(`/download?url=${encodeURIComponent(validation.normalized_url)}`);
    }
  }

  return (
    <main className="app-shell">
      <header className="hero">
        <div className="brand">
          <span className="brand-mark">VS</span>
          <span>VideoSaver</span>
        </div>
        <p className="eyebrow">X video workflow</p>
        <h1>Paste a link. Pick a resolution. Download.</h1>
        <p className="hero-copy">
          URL parsing and provider detection are enforced on the server. Choose the
          quality that fits your device before downloading.
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
                    className="primary-button"
                    type="button"
                    onClick={handleDownload}
                  >
                    Download
                  </button>
                )}
              </div>
            )}
          </section>
        </div>

        <div className="column">
          <section className="panel note-panel">
            <p className="eyebrow">How it works</p>
            <h2>One link, three steps</h2>
            <ul className="feature-list">
              <li>
                <strong>Validate</strong>
                <span>The server checks the host, status path, and video URL shape.</span>
              </li>
              <li>
                <strong>Choose quality</strong>
                <span>Standard tiers map to the closest progressive source X exposes.</span>
              </li>
              <li>
                <strong>Download</strong>
                <span>The file streams through an attachment endpoint with Range support.</span>
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

export default function App() {
  const [route, setRoute] = useState(readRoute);

  useEffect(() => {
    function handleRouteChange() {
      setRoute(readRoute());
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    window.addEventListener("hashchange", handleRouteChange);
    return () => window.removeEventListener("hashchange", handleRouteChange);
  }, []);

  if (route.name === "download") {
    return <DownloadPage sourceUrl={route.url} />;
  }

  return <HomePage />;
}
