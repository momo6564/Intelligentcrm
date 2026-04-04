from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from ..auth import get_session_user, login_required
from ..order_ops import (
    add_order_message,
    add_daily_update,
    add_issue,
    add_sample_request,
    advance_order_stage,
    auto_build_order_schedule,
    can_edit_order,
    can_manage_workflow,
    can_view_internal_workspace,
    create_order,
    customer_order_by_code,
    customer_portal_payload,
    dashboard_payload,
    get_ops_conn,
    get_order_detail,
    order_planner_payload,
    get_workflow_template,
    internal_workspace_payload,
    link_brand_owner_workspace,
    ops_role_for_user,
    resolve_issue,
    save_order_schedule,
    save_order_processes,
    save_schedule_default,
    share_order_with_brand_owner,
    update_stage,
    update_workflow_template,
)
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint("ops", __name__)


def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)


def _forbidden(message: str = "You do not have permission for this action."):
    return jsonify({"ok": False, "error": message}), 403


def _customer_order_id():
    raw = session.get("ops_customer_order_id")
    try:
        return int(raw or 0)
    except Exception:
        return 0


@bp.route("/ops")
@login_required()
def ops_workspace_page():
    user = get_session_user()
    return render_app(
        "ops/planner.html",
        order_id=0,
        ops_role=ops_role_for_user(user),
        can_edit_order=can_edit_order(user),
        can_manage_workflow=can_manage_workflow(user),
        can_view_internal_workspace=can_view_internal_workspace(user),
    )


@bp.route("/ops/orders/<int:order_id>/planner")
@login_required()
def ops_order_planner_page(order_id: int):
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    detail = get_order_detail(conn, workspace_id, order_id)
    if detail is None:
        return redirect(url_for("ops.ops_workspace_page"))
    return render_app(
        "ops/planner.html",
        order_id=order_id,
        ops_role=ops_role_for_user(user),
        can_edit_order=can_edit_order(user),
    )


@bp.route("/ops/track", methods=["GET", "POST"])
def ops_customer_track():
    error = ""
    if request.method == "POST":
        access_code = clean_text(request.form.get("access_code")).upper()
        if not access_code:
            error = "Access code is required."
        else:
            conn = get_ops_conn()
            order = customer_order_by_code(conn, access_code)
            if order is None:
                error = "That access code was not found."
            else:
                session["ops_customer_order_id"] = int(order["id"])
                session["ops_customer_access_code"] = access_code
                return redirect(url_for("ops.ops_customer_portal"))
    return render_template("ops/customer_login.html", error=error)


@bp.route("/ops/track/view")
def ops_customer_portal():
    order_id = _customer_order_id()
    if order_id <= 0:
        return redirect(url_for("ops.ops_customer_track"))
    return render_template("ops/customer_portal.html")


@bp.route("/ops/track/logout")
def ops_customer_logout():
    session.pop("ops_customer_order_id", None)
    session.pop("ops_customer_access_code", None)
    return redirect(url_for("ops.ops_customer_track"))


@bp.route("/api/ops/dashboard")
@login_required()
def api_ops_dashboard():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    return jsonify({"ok": True, **dashboard_payload(conn, workspace_id, int(user.get("id") or 0))})


@bp.route("/api/ops/internal")
@login_required()
def api_ops_internal():
    user = get_session_user()
    if not can_view_internal_workspace(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    return jsonify({"ok": True, **internal_workspace_payload(conn, workspace_id, int(user.get("id") or 0))})


@bp.route("/api/ops/workflow-template", methods=["GET", "POST"])
@login_required()
def api_ops_workflow_template():
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    if request.method == "GET":
        return jsonify({"ok": True, "template": get_workflow_template(conn, workspace_id, int(user.get("id") or 0))})
    if not can_manage_workflow(user):
        return _forbidden()
    payload = request.get_json(silent=True) or {}
    update_workflow_template(conn, workspace_id, int(user.get("id") or 0), payload)
    conn.commit()
    return jsonify({"ok": True, "template": get_workflow_template(conn, workspace_id, int(user.get("id") or 0))})


@bp.route("/api/ops/orders", methods=["POST"])
@login_required()
def api_ops_create_order():
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        order_id = create_order(conn, workspace_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "order_id": order_id, "planner_url": url_for("ops.ops_order_planner_page", order_id=order_id)})


@bp.route("/api/ops/orders/<int:order_id>")
@login_required()
def api_ops_order_detail(order_id: int):
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    detail = get_order_detail(conn, workspace_id, order_id)
    if detail is None:
        return jsonify({"ok": False, "error": "Order not found."}), 404
    return jsonify({"ok": True, **detail})


@bp.route("/api/ops/orders/<int:order_id>/planner", methods=["GET", "POST"])
@login_required()
def api_ops_order_planner(order_id: int):
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    if request.method == "GET":
        payload = order_planner_payload(conn, workspace_id, order_id)
        if payload is None:
            return jsonify({"ok": False, "error": "Order not found."}), 404
        return jsonify({"ok": True, **payload})
    if not can_edit_order(user):
        return _forbidden()
    payload = request.get_json(silent=True) or {}
    try:
        saved_days = save_order_schedule(conn, workspace_id, order_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "saved_days": saved_days})


@bp.route("/api/ops/orders/<int:order_id>/processes", methods=["POST"])
@login_required()
def api_ops_order_processes(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        stages = save_order_processes(conn, workspace_id, order_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "stages": stages})


@bp.route("/api/ops/orders/<int:order_id>/planner/auto", methods=["POST"])
@login_required()
def api_ops_order_planner_auto(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    duration_raw = clean_text(payload.get("planned_duration_days"))
    duration_days = int(duration_raw) if duration_raw.isdigit() else 0
    conn = get_ops_conn()
    try:
        saved_days = auto_build_order_schedule(
            conn,
            workspace_id,
            order_id,
            int(user.get("id") or 0),
            duration_days=duration_days,
            use_saved_default=str(payload.get("use_saved_default", True)).lower() not in {"0", "false", "no", "off"},
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "saved_days": saved_days})


@bp.route("/api/ops/orders/<int:order_id>/planner/default", methods=["POST"])
@login_required()
def api_ops_order_planner_default(order_id: int):
    user = get_session_user()
    if not can_manage_workflow(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        saved = save_schedule_default(conn, workspace_id, order_id, int(user.get("id") or 0), clean_text(payload.get("name")))
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "saved_default": saved})


@bp.route("/api/ops/orders/<int:order_id>/updates", methods=["POST"])
@login_required()
def api_ops_add_update(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        update_id = add_daily_update(conn, workspace_id, order_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "update_id": update_id})


@bp.route("/api/ops/orders/<int:order_id>/issues", methods=["POST"])
@login_required()
def api_ops_add_issue(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        issue_id = add_issue(conn, workspace_id, order_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "issue_id": issue_id})


@bp.route("/api/ops/orders/<int:order_id>/issues/<int:issue_id>/resolve", methods=["POST"])
@login_required()
def api_ops_resolve_issue(order_id: int, issue_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        resolve_issue(conn, workspace_id, order_id, issue_id, payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "issue_id": issue_id})


@bp.route("/api/ops/orders/<int:order_id>/stages/<int:stage_id>", methods=["POST"])
@login_required()
def api_ops_update_stage(order_id: int, stage_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        update_stage(conn, workspace_id, order_id, stage_id, payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.route("/api/ops/orders/<int:order_id>/advance", methods=["POST"])
@login_required()
def api_ops_advance_order(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    conn = get_ops_conn()
    try:
        stage_id = advance_order_stage(conn, workspace_id, order_id, int(user.get("id") or 0))
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "stage_id": stage_id})


@bp.route("/api/ops/orders/<int:order_id>/messages", methods=["POST"])
@login_required()
def api_ops_add_message(order_id: int):
    user = get_session_user()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        message_id = add_order_message(
            conn,
            workspace_id,
            order_id,
            author_role="manufacturer",
            author_name=clean_text(user.get("username") or user.get("account_name")),
            message=payload.get("message"),
            is_customer_visible=1,
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "message_id": message_id})


@bp.route("/api/ops/brand-links", methods=["POST"])
@login_required()
def api_ops_link_brand_owner():
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        linked = link_brand_owner_workspace(
            conn,
            workspace_id,
            clean_text(payload.get("brand_owner_workspace_id")),
            int(user.get("id") or 0),
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "linked": linked})


@bp.route("/api/ops/orders/<int:order_id>/share", methods=["POST"])
@login_required()
def api_ops_share_order(order_id: int):
    user = get_session_user()
    if not can_edit_order(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        shared = share_order_with_brand_owner(
            conn,
            workspace_id,
            order_id,
            clean_text(payload.get("brand_owner_workspace_id")),
            int(user.get("id") or 0),
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "shared": shared})


@bp.route("/api/ops/sample-requests", methods=["POST"])
@login_required()
def api_ops_sample_request():
    user = get_session_user()
    if not can_view_internal_workspace(user):
        return _forbidden()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    conn = get_ops_conn()
    try:
        request_id = add_sample_request(conn, workspace_id, int(user.get("id") or 0), payload)
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sample_request_id": request_id})


@bp.route("/api/ops/customer/order")
def api_ops_customer_order():
    order_id = _customer_order_id()
    if order_id <= 0:
        return jsonify({"ok": False, "error": "customer session required"}), 401
    conn = get_ops_conn()
    payload = customer_portal_payload(conn, order_id)
    if payload is None:
        session.pop("ops_customer_order_id", None)
        session.pop("ops_customer_access_code", None)
        return jsonify({"ok": False, "error": "Order not found."}), 404
    return jsonify({"ok": True, **payload})


@bp.route("/api/ops/customer/message", methods=["POST"])
def api_ops_customer_message():
    order_id = _customer_order_id()
    if order_id <= 0:
        return jsonify({"ok": False, "error": "customer session required"}), 401
    conn = get_ops_conn()
    payload = customer_portal_payload(conn, order_id)
    if payload is None:
        return jsonify({"ok": False, "error": "Order not found."}), 404
    order = payload.get("order") or {}
    try:
        message_id = add_order_message(
            conn,
            clean_text(order.get("workspace_id")),
            int(order_id),
            author_role="customer",
            author_name=clean_text(order.get("customer_name") or order.get("client_name") or "Customer"),
            message=(request.get_json(silent=True) or {}).get("message"),
            is_customer_visible=1,
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "message_id": message_id})

