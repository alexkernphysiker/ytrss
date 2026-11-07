from flask import Flask, url_for
from threading import Lock
from flask import send_file
from flask import request
from flask import redirect
from flask import Response
from pathlib import PurePath
from tinyurl import get_share_url
from utils import *
from ytrss_transcribe import get_engine_map, prepare_transcription_retry
from podcast_search import *
from feed_discover import discover_rss_feeds
from config import edit_config, get_config
from transcription_queue import transcription_queues
from web_ui import page_start
from group_news import MAX_GROUPS, find_groups
from news_categories import UNCLASSIFIED, category_owners
from werkzeug.exceptions import BadRequest, Conflict, HTTPException, NotFound

 
host=get_config().get("host")
port=get_config().get("port")
url_link=get_config().get("url_link")

app = Flask(__name__)


def buttons_on_top(auto_refresh=False):
    return "<form action='/subscription' method='post'>" + \
           "<input type='submit' name='show_channel_list' value='YT channels'>" + \
           "<input type='submit' name='show_playlist_list' value='YT playlists'>" + \
           "<input type='submit' name='show_rss_list' value='RSS'>" + \
           "<input type='submit' name='auto_transcription' value='Auto-transcription'>" + \
           "<input type='submit' name='auto_download' value='Auto-downloading'>" + \
           "<input type='submit' name='grouping' value='Grouping'>" + \
            "</form>"


def service_page(content="", title="Configuration", auto_refresh=False):
    cfg = get_config()
    refresh_interval = cfg["delay-between-fetches"] if auto_refresh else None
    return page_start(f"{cfg['title']} - {title}", "service-page", refresh_interval) + f"""
<header class="service-header">
  <p class="service-brand">{escape(cfg['title'])}</p>
  <h1 class="service-title">{escape(title)}</h1>
</header>
<nav class="service-nav" aria-label="Service navigation">{buttons_on_top()}</nav>
<div class="service-content">{content}</div>
</main></body></html>"""


def _service_message(message, title="Status"):
    return service_page(
        f'<section class="service-panel"><p class="service-message">{escape(message)}</p></section>',
        title,
    )


def _search_panel(title, action, field, button="Search"):
    return f"""<section class="service-panel">
<h2><label for="{field}">{escape(title)}</label></h2>
<form class="service-search" action="{action}" method="post">
  <input type="text" name="{field}" id="{field}">
  <input type="submit" value="{escape(button, quote=True)}">
</form></section>"""


def _source_item(label, action, field=None, value=None, button=None, link=None,
                 description=None, status=""):
    label_html = f'[{escape(str(label))}]'
    if link is not None:
        label_html = f'<a href="{escape(safe_content_url(link), quote=True)}" target="_blank">{label_html}</a>'
    controls = ""
    if field is not None:
        controls += (
            f'<input type="hidden" name="{field}" class="form-control" id="{field}"'
            f' value="{escape(str(value), quote=True)}">'
        )
    if button is not None:
        controls += f'<input type="submit" value="{escape(button, quote=True)}">'
    details = f'<div class="service-description">{sanitize_html(description)}</div>' if description is not None else ""
    return (
        '<li class="service-item">'
        f'<form action="{escape(action, quote=True)}" method="post">'
        f'<span class="service-item-title">{label_html}{escape(status)}</span>{controls}</form>'
        f'{details}</li>'
    )


def _source_section(title, items):
    return (
        f'<section><h2 class="service-section-title">{escape(title)}</h2>'
        f'<ul class="service-list">{items}</ul></section>'
    )


def _back_form(action):
    return f'<form class="service-back" action="{action}" method="get"><input type="submit" value="Back"></form>'


def _number_field(name, label, value, minimum, maximum):
    return (
        f'<div class="service-field"><label for="{name}">{escape(label)}</label>'
        f'<input type="number" id="{name}" name="{name}" min="{minimum}" max="{maximum}"'
        f' value="{escape(str(value), quote=True)}"></div>'
    )


def _select_field(name, label, options):
    return (
        f'<div class="service-field"><label for="{name}">{escape(label)}</label>'
        f'<select id="{name}" name="{name}">{options}</select></div>'
    )


def _log_page(command, title):
    proc = subprocess.run(command, shell=True, capture_output=True, timeout=20)
    content = f'<section class="service-panel"><pre class="service-log">{escape(proc.stdout.decode().strip())}</pre></section>'
    return service_page(content, title, auto_refresh=True)


@app.errorhandler(HTTPException)
def service_error(error):
    response = error.get_response()
    response.set_data(_service_message(error.description, f"{error.code} {error.name}"))
    return response

@app.route("/subscription", methods=['GET','POST'])
def subscription():
    if request.method == 'POST':
        for action in request.form.keys():
            return redirect(url_for(action))
    else:
        return service_page(title="Subscriptions")

@app.route("/config")
def config_page():
    return service_page(title="Configuration")

_GROUPING_JOB_LOCK = Lock()


def _category_redirect(name=None):
    values = {"_anchor": "category-editor"}
    if name is not None:
        values["category"] = name
    return redirect(url_for("grouping", **values))


def _checked_category_owners(settings):
    try:
        return category_owners(settings["topics"], settings["categories"])
    except ValueError as error:
        raise BadRequest(str(error)) from error


def _require_category(settings, name):
    if name not in settings["categories"]:
        raise NotFound("This category no longer exists. Please reload the grouping page.")
    return settings["categories"][name]


def _new_category_name(field):
    name = request.form.get(field, "").strip()
    if not name:
        raise BadRequest("The category name must not be empty.")
    if name == UNCLASSIFIED:
        raise BadRequest("The name unclassified is reserved.")
    return name


def _grouping_page():
    settings = get_config()
    topics, categories = settings["topics"], settings["categories"]
    owners = _checked_category_owners(settings)
    selected = request.args.get("category")
    if selected not in categories:
        selected = next(iter(categories), None)
    category_items = []
    for name in categories:
        current = ' aria-current="page"' if name == selected else ""
        active = " is-selected" if name == selected else ""
        category_items.append(
            f'<li class="category-item{active}">'
            f'<a class="category-select" href="{escape(url_for("grouping", category=name, _anchor="category-editor"), quote=True)}"{current}>'
            f'{escape(name)}</a>'
            f'<form class="category-edit" action="{url_for("rename_category")}" method="post">'
            f'<input type="hidden" name="category" value="{escape(name, quote=True)}">'
            f'<input type="text" name="new_name" value="{escape(name, quote=True)}"'
            ' aria-label="Category name" required>'
            '<button type="submit">Rename</button>'
            f'<button type="submit" formaction="{url_for("delete_category")}" formnovalidate>Delete</button>'
            '</form></li>'
        )
    category_list = "".join(category_items) or '<li class="service-description">No categories created.</li>'

    def group_choices(groups, disabled=False):
        state = " disabled" if disabled else ""
        return "".join(
            '<label class="group-choice">'
            f'<input type="checkbox" name="group" value="{escape(name, quote=True)}"{state}>'
            f'<span>{escape(name)}</span></label>'
            for name in groups
        )

    assigned = categories.get(selected, [])
    available = [name for name in topics if name not in owners]
    target = escape(selected or "", quote=True)
    assigned_fields = group_choices(assigned) or '<p class="service-description">No groups assigned.</p>'
    available_fields = group_choices(available, selected is None) or '<p class="service-description">No available groups.</p>'
    remove_disabled = "" if assigned else " disabled"
    add_disabled = "" if selected is not None and available else " disabled"
    selection = escape(selected) if selected is not None else "No category selected"
    content = (
        '<section class="service-panel"><h2>Automatic groups</h2>'
        f'<form id="grouping-auto-config" class="grouping-auto-form" action="{url_for("auto_config_groups")}" method="post">'
        '<div class="grouping-count-field"><label for="group-count">Number of automatic groups</label>'
        f'<input type="number" id="group-count" name="group_count" min="0" max="{MAX_GROUPS}"'
        f' step="1" value="{len(topics)}" required></div>'
        '<input type="submit" value="auto-config"></form>'
        '<p class="service-description">Lower-level groups keep their generated names. '
        'Auto-config replaces the groups and clears every category assignment. '
        'Use 0 to clear all automatic groups. Processing may take some time.</p></section>'
        f'<form id="grouping-opml-export" action="{url_for("categorized_feed_list")}" method="get">'
        '<input type="hidden" name="download" value="1">'
        '<input type="submit" value="Save OPML"></form>'
        '<section id="category-editor" class="grouping-columns" aria-label="Category editor">'
        '<section class="service-panel grouping-column"><h2>Categories</h2>'
        f'<form class="category-create" action="{url_for("create_category")}" method="post">'
        '<input type="text" name="name" aria-label="New category name" placeholder="New category name" required>'
        '<button type="submit">Create category</button></form>'
        f'<ul class="category-items">{category_list}</ul></section>'
        '<section class="service-panel grouping-column"><h2>Assigned automatic groups</h2>'
        f'<p class="service-description">{selection}</p>'
        f'<form action="{url_for("remove_category_groups")}" method="post">'
        f'<input type="hidden" name="category" value="{target}">'
        f'<div class="group-choices">{assigned_fields}</div>'
        f'<button type="submit"{remove_disabled}>Remove selected</button></form></section>'
        '<section class="service-panel grouping-column"><h2>Available automatic groups</h2>'
        '<p class="service-description">These groups do not belong to any category.</p>'
        f'<form action="{url_for("add_category_groups")}" method="post">'
        f'<input type="hidden" name="category" value="{target}">'
        f'<div class="group-choices">{available_fields}</div>'
        f'<button type="submit"{add_disabled}>Add selected</button></form></section></section>'
        '<script>document.getElementById("grouping-auto-config").addEventListener("submit",function(){'
        'const button=this.querySelector(\'input[type="submit"]\');'
        'button.disabled=true;button.value="Working…";});</script>'
    )
    return service_page(content, "Grouping")


@app.route("/grouping")
def grouping():
    return _grouping_page()


@app.route("/grouping/auto-config", methods=["POST"])
def auto_config_groups():
    try:
        count = int(request.form.get("group_count", ""))
    except ValueError as error:
        raise BadRequest("The number of groups must be a whole number.") from error
    if not 0 <= count <= MAX_GROUPS:
        raise BadRequest(f"The number of groups must be between 0 and {MAX_GROUPS}.")
    if not _GROUPING_JOB_LOCK.acquire(blocking=False):
        raise Conflict("Automatic grouping is already running. Please try again later.")
    try:
        previous = get_config()
        try:
            groups = find_groups(count)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        with edit_config() as cfg:
            if (any(cfg[key] != previous[key] for key in ("topics", "categories", "grouping_stop_words"))
                    or any(list(cfg[key]) != list(previous[key]) for key in ("topics", "categories"))):
                raise Conflict("Group or category settings changed while auto-config was running. Please try again.")
            cfg["topics"] = groups
            cfg["categories"] = {name: [] for name in cfg["categories"]}
    finally:
        _GROUPING_JOB_LOCK.release()
    return _category_redirect()


@app.route("/grouping/categories/create", methods=["POST"])
def create_category():
    name = _new_category_name("name")
    with edit_config() as settings:
        _checked_category_owners(settings)
        if name in settings["categories"]:
            raise Conflict("A category with this name already exists.")
        settings["categories"][name] = []
    return _category_redirect(name)


@app.route("/grouping/categories/rename", methods=["POST"])
def rename_category():
    name = request.form.get("category", "")
    new_name = _new_category_name("new_name")
    with edit_config() as settings:
        _checked_category_owners(settings)
        _require_category(settings, name)
        if new_name != name and new_name in settings["categories"]:
            raise Conflict("A category with this name already exists.")
        settings["categories"] = {
            new_name if current == name else current: groups
            for current, groups in settings["categories"].items()
        }
    return _category_redirect(new_name)


@app.route("/grouping/categories/delete", methods=["POST"])
def delete_category():
    name = request.form.get("category", "")
    with edit_config() as settings:
        _checked_category_owners(settings)
        _require_category(settings, name)
        del settings["categories"][name]
    return _category_redirect()


def _requested_groups():
    groups = request.form.getlist("group")
    if not groups or len(groups) != len(set(groups)):
        raise BadRequest("Select at least one automatic group, without duplicates.")
    return groups


@app.route("/grouping/categories/add-groups", methods=["POST"])
def add_category_groups():
    name = request.form.get("category", "")
    groups = _requested_groups()
    with edit_config() as settings:
        owners = _checked_category_owners(settings)
        assigned = _require_category(settings, name)
        for group in groups:
            if group not in settings["topics"]:
                raise NotFound("This automatic group no longer exists.")
            if group in owners:
                raise Conflict("A selected group is already assigned. Please reload the grouping page.")
        assigned.extend(groups)
    return _category_redirect(name)


@app.route("/grouping/categories/remove-groups", methods=["POST"])
def remove_category_groups():
    name = request.form.get("category", "")
    groups = _requested_groups()
    with edit_config() as settings:
        _checked_category_owners(settings)
        assigned = _require_category(settings, name)
        if any(group not in assigned for group in groups):
            raise Conflict("Group assignments changed. Please reload the grouping page.")
        removing = set(groups)
        settings["categories"][name] = [group for group in assigned if group not in removing]
    return _category_redirect(name)


@app.route("/logs/server")
def logs_server():
    return _log_page("cat ytrss.log | tail -n 50", "Server logs")
@app.route("/logs/transcription")
def logs_transcription():
    return _log_page("cat ytrss_transcribe.log | tail -n 50", "Transcription logs")
@app.route("/logs/download")
def logs_download():
    return _log_page("cat ytrss_upd.log | tail -n 100", "Download logs")

##### channel subscriptions

@app.route("/show_channel_list")
def show_channel_list():
    cfg = get_config()
    search = _search_panel("Search channels", "/search/channel", "channel_search_query") if cfg.get("google_search_api_key") != "" else ""
    items = "".join(
        _source_item(get_channel_name(channel_id), "/unsubscribe/channel", "source_id", channel_id, "Unsubscribe")
        for channel_id in cfg["channel_subscriptions"]
    )
    return service_page(search + _source_section("Subscribed channels", items), "YT channels")

@app.route("/get_chan_info", methods=['POST'])
def get_chan_info():
    channel_id = request.form['channel_id']
    if not channel_id:
        return _service_message("No channel ID provided.", "Channel information")
    subscribed = channel_id in get_config()["channel_subscriptions"]
    channel_name = get_channel_name(channel_id)
    state = "is subscribed" if subscribed else "is not subscribed"
    action = "unsubscribe" if subscribed else "subscribe"
    button = "Unsubscribe" if subscribed else "Subscribe"
    content = (
        f'<section class="service-panel"><p>Channel {escape(repr(channel_name))} {state}.</p>'
        f'<form action="/{action}/channel/{escape(channel_id, quote=True)}" method="post">'
        f'<input type="submit" value="{button}"></form></section>'
        + _back_form("/show_channel_list")
    )
    return service_page(content, "Channel information")

@app.route("/subscribe/channel", methods=['POST'])
def subscribe_channel():
    source_id = request.form['source_id']
    #if get_channel_name(source_id)=="":
    #    return buttons_on_top() + f"cannot obtain the channel {source_id}"
    with edit_config() as cfg:
        sources_list = cfg["channel_subscriptions"]
        if source_id not in sources_list:
            sources_list.append(source_id)
    return redirect(url_for('show_channel_list'))

@app.route("/unsubscribe/channel", methods=['POST'])
def unsubscribe_channel():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        sources_list = cfg["channel_subscriptions"]
        if source_id in sources_list:
            sources_list.remove(source_id)
    return redirect(url_for('show_channel_list'))

@app.route("/search/channel", methods=['POST'])
def search_channel():
    query = request.form['channel_search_query']
    cfg = get_config()
    items = ""
    for title, channel_id, descr in search_youtube_channel_api(query, cfg.get("google_search_api_key")):
        channel_link = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
        if channel_id not in cfg["channel_subscriptions"]:
            items += _source_item(title, "/subscribe/channel", "source_id", channel_id, "Subscribe", channel_link, descr)
        else:
            items += _source_item(title, "/subscribe/channel", link=channel_link, status=" (is subscribed)")
    return service_page(_source_section("Search results", items) + _back_form("/show_channel_list"), "Channel search")


#### playlist subscriptions
@app.route("/show_playlist_list")
def show_playlist_list():
    cfg = get_config()
    search = _search_panel("Search playlists", "/search/playlist", "playlist_search_query") if cfg.get("google_search_api_key") != "" else ""
    items = "".join(
        _source_item(get_playlist_name(playlist_id), "/unsubscribe/playlist", "source_id", playlist_id, "Unsubscribe")
        for playlist_id in cfg["playlist_subscriptions"]
    )
    return service_page(search + _source_section("Subscribed playlists", items), "YT playlists")

@app.route("/subscribe/playlist", methods=['POST'])
def subscribe_playlist():
    source_id = request.form['source_id']
    #if get_playlist_name(source_id)=="":
    #    return buttons_on_top() + f"cannot obtain the playlist {source_id}"
    with edit_config() as cfg:
        playlists_list = cfg["playlist_subscriptions"]
        if source_id not in playlists_list:
            playlists_list.append(source_id)
    return redirect(url_for('show_playlist_list'))

@app.route("/unsubscribe/playlist", methods=['POST'])
def unsubscribe_playlist():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        playlists_list = cfg["playlist_subscriptions"]
        if source_id in playlists_list:
            playlists_list.remove(source_id)
    return redirect(url_for('show_playlist_list'))

@app.route("/search/playlist", methods=['POST'])
def search_playlist():
    query = request.form['playlist_search_query']
    cfg = get_config()
    items = ""
    for title, playlist_id, descr in search_youtube_playlist_api(query, cfg.get("google_search_api_key")):
        playlist_link = f"https://www.youtube.com/playlist?list={playlist_id}" if playlist_id else ""
        if playlist_id not in cfg["playlist_subscriptions"]:
            items += _source_item(title, "/subscribe/playlist", "source_id", playlist_id, "Subscribe", playlist_link, descr)
        else:
            items += _source_item(title, "/subscribe/playlist", link=playlist_link, status="(Subscribed)")
    return service_page(_source_section("Search results", items) + _back_form("/show_playlist_list"), "Playlist search")


#auto-downloading management for channels and playlists
@app.route("/auto_download")
def auto_download():
    cfg = get_config()
    enabled, disabled = "", ""
    for source_id in cfg["channel_subscriptions"] + cfg["playlist_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in cfg["channel_subscriptions"] else get_playlist_name(source_id)
        if source_id in cfg["sources_with_disabled_downloading"]:
            disabled += _source_item(source_name, "/downloading/enable", "source_id", source_id, "Enable")
        else:
            enabled += _source_item(source_name, "/downloading/disable", "source_id", source_id, "Disable")
    fields = "".join(_number_field(name, label, cfg[name], minimum, maximum) for name, label, minimum, maximum in (
        ("max_days", "Keep downloaded items (days):", 7, 90),
        ("deliver_days", "RSS contains items from (days):", 1, 90),
        ("duplicate_detection_threshold", "Duplicate Detection Threshold:", 0, 100),
        ("delay-between-fetches", "Typical delay between fetches:", 10, 90),
    ))
    settings = (
        '<section class="service-panel"><h2>Download settings</h2>'
        f'<form class="service-settings" action="/download-cfg" method="post">{fields}'
        '<input type="submit" value="Save config"></form></section>'
    )
    logs = (
        '<div class="service-log-actions">'
        '<form action="/logs/download"><input type="submit" value="View download logs"></form>'
        '<form action="/logs/server"><input type="submit" value="View server logs"></form></div>'
    )
    return service_page(
        settings + logs
        + _source_section("Subscribed channels and playlists with enabled auto-downloading", enabled)
        + _source_section("Subscribed channels and playlists with disabled auto-downloading", disabled),
        "Auto-downloading",
    )

@app.route("/downloading/disable", methods=['POST'])
def disable_downloading():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        sources_list = cfg["sources_with_disabled_downloading"]
        if source_id not in sources_list:
            sources_list.append(source_id)
    return redirect(url_for('auto_download'))

@app.route("/downloading/enable", methods=['POST'])
def enable_downloading():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        sources_list = cfg["sources_with_disabled_downloading"]
        if source_id in sources_list:
            sources_list.remove(source_id)
    return redirect(url_for('auto_download'))
@app.route("/download-cfg", methods=['POST'])
def download_cfg():
    try:
        delay = int(request.form['delay-between-fetches'])
    except ValueError as error:
        raise BadRequest("Delay between fetches must be a whole number.") from error
    with edit_config() as cfg:
        cfg["max_days"] = int(request.form['max_days'])
        cfg["deliver_days"] = int(request.form['deliver_days'])
        cfg["duplicate_detection_threshold"] = int(request.form['duplicate_detection_threshold'])
        cfg["delay-between-fetches"] = delay
    return redirect(url_for('auto_download'))


#auto-transcription management for channels and playlists
@app.route("/auto_transcription")
def auto_transcription():
    cfg = get_config()
    enabled, disabled = "", ""
    for source_id in cfg["channel_subscriptions"] + cfg["playlist_subscriptions"] + cfg["rss_subscriptions"]:
        source_name = get_channel_name(source_id) if source_id in cfg["channel_subscriptions"] else get_playlist_name(source_id) if source_id in cfg["playlist_subscriptions"] else get_rss_name(source_id)
        if source_id in cfg["sources_with_disabled_auto_transcription"]:
            disabled += _source_item(source_name, "/auto-transcription/enable", "source_id", source_id, "Enable")
        else:
            enabled += _source_item(source_name, "/auto-transcription/disable", "source_id", source_id, "Disable")
    engines_str, engines_rss_str = "", ""
    for engine, engine_text in get_engine_map().items():
        option = f'<option value="{escape(engine, quote=True)}"'
        label = f'>{escape(engine_text)}</option>'
        engines_str += option + (' selected' if engine == cfg["auto_transcript_engine"] else '') + label
        engines_rss_str += option + (' selected' if engine == cfg["auto_transcript_engine_rss"] else '') + label
    fields = (
        _select_field("default_engine", "Youtube summarization engine:", engines_str)
        + _select_field("auto_transcript_engine_rss", "RSS summarization engine:", engines_rss_str)
    )
    fields += "".join(_number_field(name, label, cfg[name], minimum, maximum) for name, label, minimum, maximum in (
        ("auto_transcript_hours", "auto-transcript items not older than (Hr):", 3, 96),
        ("wait_for_download_hours", "wait for subtitles (Hr):", 0, 6),
        ("duplicate_detection_threshold_transcription", "Duplicate Detection Threshold for Transcription:", 0, 100),
        ("summarize_min_length", "Minimum text length for summarization:", 0, 100000),
    ))
    settings = (
        '<section class="service-panel"><h2>Transcription settings</h2>'
        f'<form class="service-settings" action="/auto-transcription-cfg" method="post">{fields}'
        '<input type="submit" value="Save config"></form></section>'
    )
    logs = '<form action="/logs/transcription"><input type="submit" value="View logs"></form>'
    return service_page(
        settings + logs
        + _source_section("Subscribed sources with enabled auto-transcription", enabled)
        + _source_section("Subscribed sources with disabled auto-transcription", disabled),
        "Auto-transcription",
    )

@app.route("/auto-transcription/disable", methods=['POST'])
def disable_auto_transcription():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        sources_list = cfg["sources_with_disabled_auto_transcription"]
        if source_id not in sources_list:
            sources_list.append(source_id)
    return redirect(url_for('auto_transcription'))

@app.route("/auto-transcription/enable", methods=['POST'])
def enable_auto_transcription():
    source_id = request.form['source_id']
    with edit_config() as cfg:
        sources_list = cfg["sources_with_disabled_auto_transcription"]
        if source_id in sources_list:
            sources_list.remove(source_id)
    return redirect(url_for('auto_transcription'))

@app.route("/auto-transcription-cfg", methods=['POST'])
def auto_transcription_cfg():
    with edit_config() as cfg:
        cfg["auto_transcript_engine"] = request.form['default_engine']
        cfg["auto_transcript_engine_rss"] = request.form['auto_transcript_engine_rss']
        cfg["auto_transcript_hours"] = int(request.form['auto_transcript_hours'])
        cfg["wait_for_download_hours"] = int(request.form['wait_for_download_hours'])
        cfg["duplicate_detection_threshold_transcription"] = int(request.form['duplicate_detection_threshold_transcription'])
        cfg["summarize_min_length"] = int(request.form['summarize_min_length'])
    return redirect(url_for('auto_transcription'))


### video transcription
@app.route("/transcribe/<engine>/<filename>")
def transcribe(engine, filename):
    engines = get_engine_map()
    if engine not in engines:
        return _service_message(f"Unknown transcription engine {engine}", "Transcription")

    def remove_old_output():
        prepare_transcription_retry(filename, engine)
        Path(f"yt-video/{filename}.log").unlink(missing_ok=True)
        Path(f"yt-video/{filename}.txt").unlink(missing_ok=True)

    if transcription_queues.enqueue(engine, filename, prepare=remove_old_output):
        return _service_message(f"Scheduled {engines[engine]} for video {filename}.", "Transcription")
    else:
        return _service_message(f"{engines[engine]} for video {filename} was already scheduled.", "Transcription")


@app.route("/remove_transcription/<filename>")
def remove_transcription(filename):
    transcription_path = f"yt-video/{filename}.txt"
    log_path = f"yt-video/{filename}.log"
    if os.path.exists(transcription_path):
        os.remove(transcription_path)
    if os.path.exists(log_path):
        os.remove(log_path)
    return _service_message(f"Transcription for video {filename} has been removed.", "Transcription")



##### rss subscriptions
@app.route("/show_rss_list")
def show_rss_list():
    search = '<div class="service-search-grid">' + ''.join(
        _search_panel(title, action, field, button) for title, action, field, button in (
            ("Search Podcasts with iTunes API", "/search/rss", "podcast_search_query", "Search"),
            ("Search RSS with Feedly", "/search/rss2", "rss_search_query", "Search"),
            ("Discover RSS by site URL", "/search/rss3", "site_url", "Search"),
            ("Add direct RSS link", "/subscribe/rss", "rss_link", "Add"),
        )
    ) + '</div>'
    items = ''.join(
        _source_item(get_rss_name(link), "/unsubscribe/rss", "rss_link", link, "Unsubscribe")
        for link in get_config()["rss_subscriptions"]
    )
    return service_page(search + _source_section("Subscribed RSS podcasts", items), "RSS podcasts")

@app.route("/subscribe/rss", methods=['POST'])
def subscribe_rss():
    link = request.form['rss_link']
    if get_rss_name(link)=="":
        return _service_message(f"cannot parse the rss {link}", "RSS subscription")
    with edit_config() as cfg:
        sources_list = cfg["rss_subscriptions"]
        if link not in sources_list:
            sources_list.append(link)
    return redirect(url_for('show_rss_list'))

@app.route("/unsubscribe/rss", methods=['POST'])
def unsubscribe_rss():
    link = request.form['rss_link']
    with edit_config() as cfg:
        sources_list = cfg["rss_subscriptions"]
        if link in sources_list:
            sources_list.remove(link)
    return redirect(url_for('show_rss_list'))

@app.route("/search/rss", methods=['POST'])
def search_rss():
    query = request.form['podcast_search_query']
    subscriptions = get_config()["rss_subscriptions"]
    items = ""
    for title, link, descr, url in search_podcast_itunes_api(query):
        if link not in subscriptions:
            items += _source_item(title, "/subscribe/rss", "rss_link", link, "Subscribe", url, descr)
        else:
            items += _source_item(title, "/subscribe/rss", link=url, status="(Subscribed)")
    return service_page(_source_section("Search results", items) + _back_form("/show_rss_list"), "Podcast search")

@app.route("/search/rss2", methods=['POST'])
def search_rss2():
    query = request.form['rss_search_query']
    subscriptions = get_config()["rss_subscriptions"]
    items = ""
    for title, link, descr, url in search_rss_feeds(query):
        if link not in subscriptions:
            items += _source_item(title, "/subscribe/rss", "rss_link", link, "Subscribe", url, descr)
        else:
            items += _source_item(title, "/subscribe/rss", link=url, status="(Subscribed)")
    return service_page(_source_section("Search results", items) + _back_form("/show_rss_list"), "RSS search")

@app.route("/search/rss3", methods=['POST'])
def search_rss3():
    query = request.form['site_url']
    subscriptions = get_config()["rss_subscriptions"]
    items = ""
    for title, link, descr, url in discover_rss_feeds(query):
        if link not in subscriptions:
            items += _source_item(title, "/subscribe/rss", "rss_link", link, "Subscribe", url, descr)
        else:
            items += _source_item(title, "/subscribe/rss", link=url, status="(Subscribed)")
    return service_page(_source_section("Search results", items) + _back_form("/show_rss_list"), "RSS discovery")


### atom feed generation
@app.route("/feed")
def yt_feed():
    global url_link
    return Response(generate_atom_feed(url_link, False), mimetype='application/rss+xml')

@app.route("/feed/categorized")
def categorized_feed_list():
    response = Response(generate_categorized_opml(url_link), mimetype="text/x-opml")
    if request.args.get("download") == "1":
        response.headers.set("Content-Disposition", "attachment", filename="ytrss.opml")
    return response


@app.route("/feed/category/unclassified", defaults={"category_id": UNCLASSIFIED})
@app.route("/feed/category/<int:category_id>")
def category_feed(category_id):
    return Response(
        generate_atom_feed(url_link, False, category_id=category_id),
        mimetype="application/rss+xml",
    )


@app.route("/file/<path:filename>.<extension>")
def download(filename, extension):
    file_path = media_file_path(filename)
    media_info = probe_media(file_path)
    if media_info is None:
        raise NotFound("Media file could not be read.")
    _, media_type = detect_mimetype(media_info)
    return return_file(
        file_path, mimetype=media_type,
        download_name=f"{file_path.name}.{extension}",
    )

@app.route("/read")
def read_list_page():
    global url_link
    return generate_transcriptions_list_page(url_link)

@app.route("/read/category", methods=['POST'])
def read_list_page_category():
    global url_link
    category = request.form['category']
    return generate_transcriptions_list_page(url_link, category_request=category)

@app.route("/read/search", methods=['POST'])
def read_list_page_search():
    global url_link
    search_request = request.form['search_request']
    return generate_transcriptions_list_page(url_link, search_request=search_request)

@app.route("/read/<filename>")
def read_transcription(filename):
    global url_link
    return generate_transcription_page(url_link, filename)

@app.route("/read_ext/<filename>/share")
def read_transcription_share(filename):
    global url_link
    get_share_url(filename, force=True)
    return redirect(url_for('read_transcription', filename=filename))

@app.route("/read/<filename>/full")
def read_full_transcription(filename):
    global url_link
    return generate_full_transcription_page(url_link, filename)

if __name__ == "__main__":
    app.run(host=host, port=port)
