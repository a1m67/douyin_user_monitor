window.ShortDramaAPI = {
  async request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase()) && !headers.Authorization && !headers.authorization) {
      const csrf = document.cookie.split(";").map(item => item.trim()).find(item => item.startsWith("short_drama_csrf="));
      if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf.slice("short_drama_csrf=".length));
    }
    const response = await fetch(`/api/short-drama${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && location.pathname !== "/login") {
      location.assign("/login");
      throw new Error("登录已失效");
    }
    if (!response.ok) throw new Error(data.detail || "请求失败");
    return data;
  },
};
