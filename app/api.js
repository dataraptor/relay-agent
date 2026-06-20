/**
 * Relay API client (Split 08 R2) — a thin transport layer over the Split 07 HTTP routes.
 *
 * No business logic: just `fetch` + parsing of the structured error envelope
 * (`{error:{type,message,provider,env_var,retriable}}`) into an Error the UI can surface
 * (missing-key → the banner string; everything else → a clean message; a dead server → a
 * network error). Pure and dependency-free so it runs both in the browser (as `window.RelayApi`)
 * and under Node's test runner (as a CommonJS module).
 */
(function (root, factory) {
  var mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.RelayApi = mod;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /** The human-facing banner/message text for an error (env var appended for missing keys). */
  function bannerText(err) {
    if (err.type === "missing_key") {
      var base = err.message || "API key missing";
      if (err.env_var && base.indexOf(err.env_var) === -1) {
        return base + " (set " + err.env_var + ")";
      }
      return base;
    }
    if (err.type === "network") {
      return err.message || "Cannot reach the Relay API — is the server running?";
    }
    return err.message || "Something went wrong talking to the API.";
  }

  /** Build a typed Error from a parsed error envelope body (or a bare object). */
  function shapeError(status, envelope) {
    envelope = envelope || {};
    var err = new Error(envelope.message || "request failed (" + status + ")");
    err.name = "RelayApiError";
    err.status = status;
    err.type = envelope.type || "error";
    err.provider = envelope.provider || null;
    err.env_var = envelope.env_var || null;
    err.retriable = envelope.retriable === undefined ? false : envelope.retriable;
    err.isMissingKey = err.type === "missing_key";
    err.bannerText = bannerText(err);
    return err;
  }

  function networkError() {
    var err = new Error("Cannot reach the Relay API — is the server running?");
    err.name = "RelayApiError";
    err.status = 0;
    err.type = "network";
    err.provider = null;
    err.env_var = null;
    err.retriable = true;
    err.isMissingKey = false;
    err.bannerText = bannerText(err);
    return err;
  }

  /**
   * Construct a client.
   * @param {{baseUrl?: string, fetch?: Function}} [opts] — same-origin (`""`) by default;
   *   `fetch` is injectable so the test suite drives canned responses with no network.
   */
  function makeClient(opts) {
    opts = opts || {};
    var baseUrl = opts.baseUrl != null ? opts.baseUrl : "";
    var fetchImpl = opts.fetch || (typeof fetch !== "undefined" ? fetch : null);

    function request(path, init) {
      if (!fetchImpl) {
        return Promise.reject(new Error("no fetch implementation available"));
      }
      return fetchImpl(baseUrl + path, init).then(
        function (res) {
          return res.json().then(
            function (body) {
              return finish(res, body);
            },
            function () {
              return finish(res, null);
            }
          );
        },
        function () {
          throw networkError();
        }
      );
    }

    function finish(res, body) {
      if (!res.ok) {
        var envelope = body && body.error ? body.error : body || {};
        throw shapeError(res.status, envelope);
      }
      return body;
    }

    function getJson(path) {
      return request(path, { method: "GET" });
    }
    function postJson(path, payload) {
      return request(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    return {
      getConfig: function () {
        return getJson("/config");
      },
      getExamples: function () {
        return getJson("/examples");
      },
      health: function () {
        return getJson("/health");
      },
      handle: function (body) {
        return postJson("/handle", body);
      },
      approve: function (body) {
        return postJson("/approve", body);
      },
    };
  }

  return { makeClient: makeClient, shapeError: shapeError, bannerText: bannerText };
});
