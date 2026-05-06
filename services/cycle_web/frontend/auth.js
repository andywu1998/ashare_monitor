(function () {
  const LOGIN_PATH = "/ui/login.html";
  const rawFetch = window.fetch.bind(window);

  function toLogin() {
    const current = window.location.pathname + window.location.search;
    const next = encodeURIComponent(current || "/ui/");
    window.location.href = `${LOGIN_PATH}?next=${next}`;
  }

  async function apiFetch(input, init) {
    const opts = Object.assign({}, init || {});
    opts.credentials = "same-origin";
    const resp = await rawFetch(input, opts);
    if (resp.status === 401) {
      toLogin();
      throw new Error("unauthorized");
    }
    return resp;
  }

  async function getMe() {
    const resp = await rawFetch("/api/auth/me", { credentials: "same-origin" });
    if (!resp.ok) {
      return null;
    }
    try {
      return await resp.json();
    } catch (_) {
      return null;
    }
  }

  async function requireAuth() {
    const me = await getMe();
    if (!me) {
      toLogin();
      return null;
    }
    return me;
  }

  async function logout() {
    await rawFetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
    });
    toLogin();
  }

  window.apiFetch = apiFetch;
  window.fetch = apiFetch;
  window.__auth = {
    getMe,
    requireAuth,
    logout,
  };
})();
