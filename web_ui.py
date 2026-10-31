"""Shared HTML shell and styling for the web interface."""

from html import escape


def page_start(title, main_class="", refresh_interval=None):
    refresh_meta = (
        f'<meta http-equiv="refresh" content="{escape(str(refresh_interval), quote=True)}">'
        if refresh_interval is not None else ""
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
""" + \
  f"<title>{escape(title)}</title>{refresh_meta}" + \
"""
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #f4f5f7;
      color: #1d2734;
      font-family: system-ui, sans-serif;
      line-height: 1.5;
    }
    main { max-width: 1400px; margin-inline: auto; padding: 16px; }
    .news-search {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 24px;
    }
    .news-search input, .news-search button {
      font: inherit;
      padding: 10px 14px;
      border: 1px solid #c5ced9;
      border-radius: 8px;
    }
    .news-search input { flex: 1; min-width: 0; background: #fff; }
    .news-search button { background: #fff; color: inherit; cursor: pointer; }
    .news-search button:hover { background: #e9edf4; }
    .news-categories {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 16px;
    }
    .news-categories button, .news-categories a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      max-width: 100%;
      min-height: 42px;
      padding: 8px 14px;
      border: 1px solid #c5ced9;
      border-radius: 999px;
      background: #fff;
      color: inherit;
      font: inherit;
      font-size: 0.88rem;
      text-decoration: none;
      overflow-wrap: anywhere;
      cursor: pointer;
    }
    .news-categories button:hover, .news-categories a:hover { background: #e9edf4; }
    .news-categories button[aria-pressed="true"], .news-categories a[aria-current="page"] {
      border-color: #315eca;
      background: #315eca;
      color: #fff;
    }
    .news-categories button:focus-visible, .news-categories a:focus-visible {
      outline: 3px solid #315eca;
      outline-offset: 2px;
    }
    .news-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 350px), 1fr));
      gap: 16px;
      list-style: none;
      padding: 0;
      margin: 0;
    }
    .news-card {
      overflow: hidden;
      background: #fff;
      border: 1px solid #e1e6ed;
      border-radius: 14px;
      box-shadow: 0 3px 12px #17294208;
    }
    .news-link {
      display: flex;
      align-items: center;
      gap: 14px;
      height: 100%;
      padding: 14px;
      color: inherit;
      text-decoration: none;
    }
    .news-link:hover { background: #f8faff; }
    .news-link:focus-visible { outline: 3px solid #315eca; outline-offset: -3px; }
    .news-image {
      flex: 0 0 auto;
      width: 112px;
      height: 112px;
      object-fit: cover;
      border-radius: 8px;
    }
    .news-content { flex: 1; min-width: 0; }
    .news-title {
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .news-meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
    }
    .news-time {
      color: #596575;
      font-size: 0.82rem;
    }
    .news-category {
      max-width: 100%;
      padding: 2px 8px;
      border-radius: 999px;
      background: #e8f0ff;
      color: #28456a;
      font-size: 0.75rem;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    main.reader-page { max-width: 1040px; padding-block: 24px; }
    .reader-card {
      overflow: hidden;
      background: #fff;
      border: 1px solid #e1e6ed;
      border-radius: 14px;
      box-shadow: 0 3px 12px #17294208;
    }
    .reader-header {
      display: flex;
      align-items: center;
      gap: 24px;
      padding: 28px;
    }
    .image-toggle {
      display: block;
      padding: 0;
      border: 0;
      background: transparent;
      cursor: zoom-in;
    }
    .image-toggle[aria-expanded="true"] { cursor: zoom-out; }
    .image-toggle:focus-visible { outline: 3px solid #315eca; outline-offset: 3px; }
    .image-toggle img { display: block; }
    .reader-image-toggle { flex: 0 0 auto; width: 180px; max-width: 100%; }
    .reader-header.image-expanded { flex-direction: column; align-items: stretch; }
    .reader-header.image-expanded .reader-image-toggle { width: 100%; }
    .reader-header.image-expanded .reader-image { width: 100%; height: auto; max-height: none; }
    .reader-image {
      width: 100%;
      max-width: 100%;
      max-height: 240px;
      object-fit: contain;
      border-radius: 10px;
    }
    .reader-heading { flex: 1; min-width: 0; }
    .reader-site { margin: 0 0 8px; color: #596575; overflow-wrap: anywhere; }
    .reader-title {
      margin: 0;
      font-size: clamp(1.4rem, 3vw, 2rem);
      line-height: 1.3;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    .reader-actions, .reader-footer {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 18px 28px;
      border-top: 1px solid #e1e6ed;
    }
    .reader-actions { border-bottom: 1px solid #e1e6ed; background: #f8faff; }
    .reader-actions a, .reader-footer a, .reader-share a, .copy-widget button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 44px;
      max-width: 100%;
      padding: 9px 14px;
      border: 1px solid #c5ced9;
      border-radius: 8px;
      background: #fff;
      color: #1d2734;
      font: inherit;
      line-height: 1.4;
      text-decoration: none;
      overflow-wrap: anywhere;
      cursor: pointer;
    }
    .reader-actions a:hover, .reader-footer a:hover,
    .reader-share a:hover, .copy-widget button:hover { background: #e9edf4; }
    .reader-actions a:focus-visible, .reader-footer a:focus-visible,
    .reader-share a:focus-visible, .copy-widget button:focus-visible {
      outline: 3px solid #315eca;
      outline-offset: 2px;
    }
    .reader-actions a.reader-full-text, .reader-footer a.reader-full-text,
    .reader-actions a.reader-summary, .reader-footer a.reader-summary {
      background: #315eca;
      border-color: #315eca;
      color: #fff;
      font-weight: 600;
    }
    .reader-actions a.reader-full-text:hover, .reader-footer a.reader-full-text:hover,
    .reader-actions a.reader-summary:hover, .reader-footer a.reader-summary:hover {
      background: #254ba2;
      border-color: #254ba2;
    }
    .reader-actions a.reader-back, .reader-footer a.reader-back {
      background: #e1e6ed;
      border-color: #b8c2cf;
      color: #1d2734;
    }
    .reader-actions a.reader-back:hover, .reader-footer a.reader-back:hover {
      background: #d2d9e3;
    }
    .reader-share { padding: 18px 28px; border-bottom: 1px solid #e1e6ed; }
    .copy-widget { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
    .copy-widget [role="status"] { color: #596575; overflow-wrap: anywhere; }
    .reader-text {
      max-width: calc(75ch + 56px); margin-inline: auto; padding: 28px;
      font-size: 1.05rem; line-height: 1.75; overflow-wrap: anywhere;
    }
    .reader-text > * { max-width: 100%; margin-inline: 0; }
    .reader-text p { margin-top: 0; margin-bottom: 1em; overflow-wrap: anywhere; }
    .reader-text :is(h1, h2, h3, h4, h5, h6) { line-height: 1.3; margin-block: 1.4em 0.6em; }
    .reader-text :is(ul, ol) { padding-inline-start: 1.5em; }
    .reader-text li + li { margin-top: 0.4em; }
    .reader-text blockquote { border-left: 3px solid #c5ced9; padding-left: 1em; color: #596575; }
    .reader-text table { display: block; overflow-x: auto; border-collapse: collapse; }
    .reader-text th, .reader-text td { border: 1px solid #c5ced9; padding: 8px 12px; text-align: start; }
    .reader-text > :first-child { margin-top: 0; }
    .reader-text > :last-child { margin-bottom: 0; }
    .reader-text img, .reader-text video, .reader-text iframe { max-width: 100%; }
    .reader-text img, .reader-text video { height: auto; }
    .reader-text pre { white-space: pre-wrap; overflow-wrap: anywhere; }
    @media (max-width: 640px) {
      main.reader-page { padding: 12px; }
      .reader-header { flex-direction: column; align-items: flex-start; gap: 16px; padding: 20px; }
      .reader-image-toggle { width: 128px; }
      .reader-image { max-height: 180px; }
      .reader-actions, .reader-footer, .reader-share { padding: 16px 20px; }
      .reader-text { padding: 20px; }
    }
    @media (max-width: 420px) {
      .news-image { width: 84px; height: 84px; }
    }

    main.service-page { max-width: 1200px; padding-block: 24px; }
    .service-header { margin-bottom: 20px; }
    .service-brand { margin: 0 0 6px; color: #596575; overflow-wrap: anywhere; }
    .service-title { margin: 0; font-size: clamp(1.5rem, 3vw, 2rem); overflow-wrap: anywhere; }
    .service-nav { margin-bottom: 24px; }
    .service-nav form { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .service-nav input, .service-nav a, .service-content input[type="submit"],
    .service-content button, .service-back input {
      display: inline-flex; align-items: center; justify-content: center;
      min-height: 44px; max-width: 100%; padding: 9px 14px;
      border: 1px solid #c5ced9; border-radius: 8px; background: #fff;
      color: #1d2734; font: inherit; line-height: 1.4; text-decoration: none;
      white-space: normal; cursor: pointer;
    }
    .service-nav input:hover, .service-nav a:hover,
    .service-content input[type="submit"]:hover, .service-content button:hover,
    .service-back input:hover { background: #e9edf4; }
    .service-page :is(input, select, button, a):focus-visible {
      outline: 3px solid #315eca; outline-offset: 2px;
    }
    .service-content { display: grid; gap: 20px; }
    .service-panel, .service-item {
      min-width: 0; padding: 20px; border: 1px solid #e1e6ed;
      border-radius: 14px; background: #fff; box-shadow: 0 3px 12px #17294208;
      overflow-wrap: anywhere;
    }
    .service-panel h2 { margin: 0 0 16px; font-size: 1.15rem; font-weight: 600; }
    .service-section-title { margin: 0 0 12px; font-size: 1.15rem; font-weight: 600; }
    .service-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
    .service-item form { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
    .service-item-title { flex: 1; min-width: 0; overflow-wrap: anywhere; }
    .service-item-title a { color: #315eca; }
    .service-description { margin: 12px 0 0; color: #596575; }
    .service-search { display: flex; flex-wrap: wrap; gap: 8px; }
    .service-search input[type="text"] { flex: 1; min-width: 0; }
    .service-content input:not([type="hidden"]):not([type="submit"]),
    .service-content select {
      min-height: 44px; max-width: 100%; padding: 9px 12px;
      border: 1px solid #c5ced9; border-radius: 8px;
      background: #fff; color: #1d2734; font: inherit;
    }
    .service-search-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .service-search-grid > * { min-width: 0; }
    .service-settings { display: grid; gap: 16px; }
    .service-field { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 320px); gap: 12px 24px; align-items: center; }
    .service-field label { overflow-wrap: anywhere; }
    .service-field input, .service-field select { width: 100%; min-width: 0; }
    .service-settings > input[type="submit"] { justify-self: start; }
    .service-log-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .service-log {
      margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, monospace; font-size: 0.9rem; line-height: 1.65;
    }
    .service-message { margin: 0; }
    @media (max-width: 640px) {
      main.service-page { padding: 16px 12px; }
      .service-panel, .service-item { padding: 16px; }
      .service-field, .service-search-grid { grid-template-columns: minmax(0, 1fr); }
      .service-search input[type="text"] { flex-basis: 100%; }
    }
  </style>
  <script>
    document.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-image-toggle]");
      if (!button) return;
      const container = button.closest(".reader-header");
      if (!container) return;
      const expanded = container.classList.toggle("image-expanded");
      const label = expanded ? "Collapse image" : "Enlarge image";
      button.setAttribute("aria-expanded", String(expanded));
      button.setAttribute("aria-label", label);
      button.title = label;
    });
  </script>
</head>
<body>
""" + f'<main class="{escape(main_class, quote=True)}">'
