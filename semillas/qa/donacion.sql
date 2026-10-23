-- RedVital · QA · umbrales de inventario sintéticos (db_donacion). Nunca en producción.
-- Banco Aurora (a1000000-…-0001): un umbral general por componente, más uno
-- específico para O-, el donante universal. Con el inventario vacío de QA, el
-- proceso programado genera alertas de escasez en la primera evaluación.
-- Idempotente.

INSERT INTO umbral_inventario (id, institucion_id, componente_id, grupo_sanguineo_id,
                               minimo_unidades, dias_previos_vencimiento, actualizado_por)
SELECT u.id::uuid, 'a1000000-0000-4000-8000-000000000001', c.id, g.id, u.minimo, u.dias,
       'b2000000-0000-4000-8000-000000000002'
FROM (VALUES
        ('e5000000-0000-4000-8000-000000000001', 'globulos_rojos', NULL, 5, 7),
        ('e5000000-0000-4000-8000-000000000002', 'plasma',         NULL, 5, 30),
        ('e5000000-0000-4000-8000-000000000003', 'plaquetas',      NULL, 3, 2),
        ('e5000000-0000-4000-8000-000000000004', 'globulos_rojos', 'O-', 8, 7)
     ) AS u(id, componente, grupo, minimo, dias)
JOIN componente c ON c.codigo = u.componente
LEFT JOIN grupo_sanguineo g ON g.codigo = u.grupo
ON CONFLICT ON CONSTRAINT uq_umbral DO NOTHING;
