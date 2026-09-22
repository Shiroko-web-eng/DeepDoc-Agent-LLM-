DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'checkpoints', 'checkpoint_blobs', 'checkpoint_writes'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_checkpoint_isolation ON %I', table_name);
    EXECUTE format(
      'CREATE POLICY tenant_checkpoint_isolation ON %I USING (thread_id LIKE current_setting(''app.tenant_id'', true) || '':%%'') WITH CHECK (thread_id LIKE current_setting(''app.tenant_id'', true) || '':%%'')',
      table_name
    );
  END LOOP;
END $$;
