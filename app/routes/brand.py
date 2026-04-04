from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from ..auth import get_session_user, is_brand_owner_user, login_required
from ..order_ops import (
    add_order_message,
    brand_owner_manufacturers_payload,
    brand_owner_order_detail,
    brand_owner_orders_payload,
    get_ops_conn,
    redeem_brand_owner_tracking_code,
)
from ..utils.text_utils import clean_text
from ..utils.workspace import workspace_id_for_user

bp = Blueprint("brand", __name__)


def render_app(template_name: str, **context):
    context.setdefault("me", get_session_user())
    return render_template(template_name, **context)


def _brand_api_forbidden():
    return jsonify({"ok": False, "error": "brand-owner account required"}), 403


def _brand_page_guard():
    user = get_session_user()
    if not is_brand_owner_user(user):
        return user, redirect(url_for("main.dashboard_page"))
    return user, None


@bp.route("/brand/dashboard")
@login_required()
def brand_dashboard_page():
    user, blocked = _brand_page_guard()
    if blocked:
        return blocked
    return render_app("brand/dashboard.html")


@bp.route("/brand/orders")
@login_required()
def brand_orders_page():
    user, blocked = _brand_page_guard()
    if blocked:
        return blocked
    return render_app("brand/orders.html")


@bp.route("/brand/manufacturers")
@login_required()
def brand_manufacturers_page():
    user, blocked = _brand_page_guard()
    if blocked:
        return blocked
    return render_app("brand/manufacturers.html")


@bp.route("/api/brand/dashboard")
@login_required()
def api_brand_dashboard():
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    orders_payload = brand_owner_orders_payload(conn, workspace_id)
    return jsonify({"ok": True, **orders_payload})


@bp.route("/api/brand/orders")
@login_required()
def api_brand_orders():
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    return jsonify({"ok": True, **brand_owner_orders_payload(conn, workspace_id)})


@bp.route("/api/brand/orders/redeem-code", methods=["POST"])
@login_required()
def api_brand_redeem_code():
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    payload = request.get_json(silent=True) or {}
    try:
        result = redeem_brand_owner_tracking_code(
            conn,
            workspace_id,
            clean_text(payload.get("access_code")),
            int(user.get("id") or 0),
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@bp.route("/api/brand/orders/<int:order_id>")
@login_required()
def api_brand_order_detail(order_id: int):
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    detail = brand_owner_order_detail(conn, workspace_id, order_id)
    if detail is None:
        return jsonify({"ok": False, "error": "Order not found."}), 404
    return jsonify({"ok": True, **detail})


@bp.route("/api/brand/orders/<int:order_id>/messages", methods=["POST"])
@login_required()
def api_brand_order_message(order_id: int):
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    detail = brand_owner_order_detail(conn, workspace_id, order_id)
    if detail is None:
        return jsonify({"ok": False, "error": "Order not found."}), 404
    order = detail.get("order") or {}
    payload = request.get_json(silent=True) or {}
    try:
        message_id = add_order_message(
            conn,
            clean_text(order.get("workspace_id")),
            int(order_id),
            author_role="brand_owner",
            author_name=clean_text(user.get("account_name") or user.get("username") or "Brand Owner"),
            message=payload.get("message"),
            is_customer_visible=1,
        )
        conn.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "message_id": message_id})


@bp.route("/api/brand/manufacturers")
@login_required()
def api_brand_manufacturers():
    user = get_session_user()
    if not is_brand_owner_user(user):
        return _brand_api_forbidden()
    conn = get_ops_conn()
    workspace_id = workspace_id_for_user(user)
    return jsonify({"ok": True, "manufacturers": brand_owner_manufacturers_payload(conn, workspace_id)})
