window.ShortDramaAPI = {
  async request(path, options = {}) {
    const response = await fetch(`/api/short-drama${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "请求失败");
    return data;
  },
};
