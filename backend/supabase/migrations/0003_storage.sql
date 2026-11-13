-- Phase 7a: private upload bucket. Files live under <org_id>/... and only that org can
-- upload or read them. No update/delete policies. The laptop worker uses the secret key
-- (bypasses RLS) to download.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('uploads', 'uploads', false, 5242880, array['text/csv', 'application/vnd.ms-excel'])
on conflict (id) do update set
    public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy uploads_bucket_insert on storage.objects for insert to authenticated
    with check (
        bucket_id = 'uploads'
        and (storage.foldername(name))[1] = (select private.current_org_id())::text
    );

create policy uploads_bucket_select on storage.objects for select to authenticated
    using (
        bucket_id = 'uploads'
        and (storage.foldername(name))[1] = (select private.current_org_id())::text
    );
