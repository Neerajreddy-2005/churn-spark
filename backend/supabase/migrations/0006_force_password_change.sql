-- Enforce the first-login password change on the server, not just in the browser.
-- Before: users could clear their own must_change_password flag through the Data API and keep
-- the temporary password their manager knows. Now:
--   1) users can no longer write the flag at all;
--   2) the flag clears itself only when the password really changes in auth.users;
--   3) while the flag is set, current_org_id() is NULL, so every org-scoped policy returns nothing.

drop policy profiles_clear_must_change on public.profiles;
revoke update (must_change_password) on public.profiles from authenticated;

-- Fires when Supabase Auth stores a new password hash. The admin-users function sets the flag
-- back to true right after it assigns a temporary password, so resets still force a change.
create function private.clear_must_change_password() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
    if new.encrypted_password is distinct from old.encrypted_password then
        update public.profiles set must_change_password = false where user_id = new.id;
    end if;
    return new;
end
$$;
revoke execute on function private.clear_must_change_password() from public, anon, authenticated;

create trigger on_auth_password_changed
    after update of encrypted_password on auth.users
    for each row execute function private.clear_must_change_password();

-- No organisation data until the temporary password has been replaced.
create or replace function private.current_org_id() returns uuid
language sql stable security definer set search_path = ''
as $$
    select org_id from public.profiles
    where user_id = auth.uid() and is_active and not must_change_password
$$;
