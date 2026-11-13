-- The admin-users Edge Function talks to the Data API as service_role. Because this project
-- does not auto-expose new tables, service_role has no table privileges by default (the laptop
-- worker never needed any: it writes through a direct database connection). Grant exactly the
-- operations the function performs, and nothing else. service_role bypasses RLS, so these
-- grants are the only limit on what the function's key can do through the Data API.

grant select, insert on public.organisations to service_role;
grant select, insert on public.profiles to service_role;
grant update (role, is_active, must_change_password, display_name) on public.profiles to service_role;
grant select, insert on public.audit_log to service_role;
