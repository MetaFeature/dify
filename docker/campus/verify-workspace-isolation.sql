WITH bindings AS (
  SELECT student_id, dify_account_id, dify_tenant_id
  FROM campus_workspace_bindings
),
topology AS (
  SELECT
    binding.student_id,
    binding.dify_account_id,
    binding.dify_tenant_id,
    COUNT(membership.account_id) AS member_count,
    COUNT(*) FILTER (WHERE membership.role = 'owner') AS owner_count,
    COUNT(*) FILTER (
      WHERE membership.account_id = binding.dify_account_id
        AND membership.role = 'editor'
    ) AS student_editor_count
  FROM bindings binding
  LEFT JOIN tenant_account_joins membership
    ON membership.tenant_id = binding.dify_tenant_id
  GROUP BY binding.student_id, binding.dify_account_id, binding.dify_tenant_id
),
student_memberships AS (
  SELECT
    binding.dify_account_id,
    COUNT(membership.tenant_id) AS membership_count
  FROM bindings binding
  LEFT JOIN tenant_account_joins membership
    ON membership.account_id = binding.dify_account_id
  GROUP BY binding.dify_account_id
),
owners AS (
  SELECT DISTINCT membership.account_id
  FROM bindings binding
  JOIN tenant_account_joins membership
    ON membership.tenant_id = binding.dify_tenant_id
   AND membership.role = 'owner'
),
summary AS (
  SELECT
    COUNT(*) AS binding_count,
    COUNT(DISTINCT binding.student_id) AS student_count,
    COUNT(DISTINCT binding.dify_account_id) AS account_count,
    COUNT(DISTINCT binding.dify_tenant_id) AS tenant_count,
    (
      SELECT COUNT(*)
      FROM topology
      WHERE member_count <> 2 OR owner_count <> 1 OR student_editor_count <> 1
    ) AS invalid_topology_count,
    (
      SELECT COUNT(*)
      FROM student_memberships
      WHERE membership_count <> 1
    ) AS invalid_student_membership_count,
    (SELECT COUNT(*) FROM owners) AS distinct_owner_count,
    (
      SELECT COUNT(*)
      FROM bindings bound_workspace
      JOIN tenant_account_joins membership
        ON membership.tenant_id = bound_workspace.dify_tenant_id
      JOIN campus_administrators administrator
        ON administrator.account_id = membership.account_id
    ) AS administrator_membership_count,
    COUNT(*) FILTER (WHERE student.id IS NULL) AS orphan_binding_count
  FROM bindings binding
  LEFT JOIN campus_students student ON student.id = binding.student_id
)
SELECT
  binding_count,
  student_count,
  account_count,
  tenant_count,
  invalid_topology_count,
  invalid_student_membership_count,
  distinct_owner_count,
  administrator_membership_count,
  orphan_binding_count
FROM summary;
