# Two-Sided Workspace Plan

## Product Direction
The app now needs to support two first-class workspace personas:

1. `manufacturer`
2. `brand_owner`

This turns the current manufacturer-first CRM into a two-sided platform:

- manufacturers manage production, CRM, chapters, institutions, and shared order execution
- brand owners manage their own CRM, chapters, institutions, and see all tracked orders across multiple manufacturers
- guest code tracking remains available, but it becomes a lightweight one-order access path rather than the main customer experience

## Core User Journeys

### Manufacturer
- signs up as a manufacturer
- lands on the existing manufacturer dashboard
- uses CRM, chapter explorer, institution explorer, and ops
- links one or more brand-owner workspaces
- shares selected orders with a linked brand owner
- sends updates and messages from the same order workspace

### Brand Owner
- signs up as a vendor / brand owner
- lands on a brand-owner dashboard
- sees active orders from multiple manufacturers
- opens the Orders tab to view tracked orders in a left sidebar list
- opens one order to review progress, delays, updates, files, and messages
- uses their own CRM, institutions, and chapters inside their own workspace

### Guest Customer
- enters an access code from a manufacturer
- sees one order only
- can follow updates and use the message thread

## MVP Goal
Deliver a first working two-sided system that answers:

1. which orders are active for this account
2. which manufacturer owns each order
3. which orders are delayed
4. which orders need a reply or approval

## Ops Planning Extension
The ops workspace now also needs a day-by-day production planner:

- order creation can use `planned start date + completion days`
- planning supports up to `60` days
- once an order is created, ops should land in a dedicated planner page
- each day is assigned to a workflow stage or left as a buffer / free day
- schedules can be auto-distributed evenly, edited manually, and saved as a workspace default for the same duration
- if a scheduled production day passes before its stage is completed, that day becomes overdue and the order should surface in red on the ops dashboard

## Implementation Strategy
Build on top of the current workspace-scoped app instead of replacing it.

### Preserve
- existing manufacturer accounts
- existing manufacturer dashboard
- existing CRM data model
- existing chapters and institutions explorers
- existing ops module

### Add
- account type on users
- brand-owner account records
- manufacturer-to-brand-owner linking
- shared-order access records
- brand-owner dashboard, orders, and manufacturers pages
- conditional sidebar based on account type

## Data Model

### Existing Tables Reused
- `users`
- `crm_contacts`
- `crm_tasks`
- `crm_activities`
- `ops_orders`
- `ops_order_stages`
- `ops_daily_updates`
- `ops_issues`
- `ops_comments`

### New / Extended Tables

#### `users`
Add:
- `account_type` with values `manufacturer` or `brand_owner`
- `brand_owner_id`

#### `brand_owners`
Purpose:
- store brand-owner organizations
- map workspace ownership cleanly

Fields:
- `id`
- `name`
- `contact_email`
- `workspace_id`
- `notes`
- `created_at`

#### `manufacturer_brand_links`
Purpose:
- link a manufacturer workspace to a brand-owner workspace

Fields:
- `id`
- `manufacturer_workspace_id`
- `brand_owner_workspace_id`
- `brand_owner_name`
- `linked_by_user_id`
- `created_at`
- unique on manufacturer and brand-owner workspace pair

#### `ops_order_brand_access`
Purpose:
- share one order with one or more brand-owner workspaces

Fields:
- `id`
- `order_id`
- `manufacturer_workspace_id`
- `brand_owner_workspace_id`
- `granted_by_user_id`
- `status`
- `created_at`
- unique on order and brand-owner workspace pair

## Navigation Model

### Manufacturer Sidebar
- Dashboard
- Chapters
- Vendors
- Institutions
- CRM
- Order Ops
- Team

### Brand Owner Sidebar
- Dashboard
- Orders
- Chapters
- Institutions
- CRM
- Manufacturers

## Brand Owner Pages

### Dashboard
Show:
- active orders
- delayed orders
- manufacturers in progress
- orders awaiting reply
- latest manufacturer updates

### Orders
Layout:
- left sidebar: order list grouped or filterable by manufacturer
- main area: selected order detail

For each order:
- order number and title
- manufacturer name
- current stage
- revised delivery date
- delay reason
- recent updates
- customer-visible files
- message thread

### Manufacturers
Show:
- all linked manufacturers
- active order count per manufacturer
- delayed order count per manufacturer

## Manufacturer Ops Changes

### Order Creation
Allow:
- pick CRM contact
- optionally select linked brand-owner workspace
- auto-share the order on creation

### Existing Order Detail
Add:
- current brand-owner access list
- share order to linked brand owner
- continue using the message thread for customer / brand-owner communication

## Permissions

### Manufacturer
- existing role model stays intact
- still mapped into ops roles

### Brand Owner
Start simple:
- `brand_owner_admin`
- `brand_owner_member`

For MVP:
- all signed-in brand-owner users in the same workspace can see shared orders

## Route Map

### Auth / Account Type
- `/signup`
- `/login`
- dashboard redirect based on account type

### Brand Owner UI
- `/brand/dashboard`
- `/brand/orders`
- `/brand/manufacturers`

### Brand Owner API
- `/api/brand/dashboard`
- `/api/brand/orders`
- `/api/brand/orders/<id>`
- `/api/brand/orders/<id>/messages`
- `/api/brand/manufacturers`

### Manufacturer Ops Additions
- `/api/ops/brand-links`
- `/api/ops/orders/<id>/share`

## Build Order

1. account type support in auth and `users`
2. brand-owner records and workspace links
3. account-type-aware sidebar and dashboard redirects
4. brand-owner dashboard and orders pages
5. manufacturer link + order sharing actions in ops
6. signed-in brand-owner order messaging
7. test coverage for signup, redirects, sharing, and multi-manufacturer visibility

## Out Of Scope For This MVP
- multi-brand-owner approvals on the same order
- brand-owner-specific custom CRM taxonomies
- real-time websocket notifications
- attachment uploads in brand-owner messaging
- advanced manufacturer invitation acceptance flow

## Safety Constraint
All new behavior should be additive and backward-compatible:

- current manufacturer users should continue to log in and land on the same workflow
- existing ops orders should still work without being shared
- guest tracking by access code must remain available
