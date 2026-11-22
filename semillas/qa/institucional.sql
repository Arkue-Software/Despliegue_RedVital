-- RedVital · QA · instituciones sintéticas (db_institucional). Nunca en producción.
--
-- La geografía la carga la migración V001 en todo ambiente; aquí solo van
-- instituciones de nombre evidentemente ficticio (RNI-07). Las dos primeras
-- tienen los identificadores fijos que ya usan las cuentas de Identidad y las
-- semillas de Campañas y Donación:
--   a1000000-…-0001 Banco Aurora (Medellín, 05001)
--   a1000000-…-0002 Banco Ceiba  (Bello, 05088)
-- Distribución desigual a propósito: Antioquia, Bogotá y Valle concentran la
-- mitad, y hay servicios de transfusión, que no custodian unidades (EC-39).
-- Idempotente: puede aplicarse de nuevo.

BEGIN;

INSERT INTO institucion (id, codigo, nombre, tipo, territorio_id, direccion, creado_por)
SELECT i.id::uuid, i.codigo, i.nombre, i.tipo, t.id, i.direccion, 'b2000000-0000-4000-8000-000000000004'
FROM (VALUES
  ('a1000000-0000-4000-8000-000000000001', 'QA-05001-001', 'Banco de Sangre Aurora (ficticio)',        'banco_de_sangre',      '05001', 'Carrera 51 # 10-20, Medellín'),
  ('a1000000-0000-4000-8000-000000000002', 'QA-05088-001', 'Banco de Sangre Ceiba (ficticio)',         'banco_de_sangre',      '05088', 'Calle 50 # 49-15, Bello'),
  ('a1000000-0000-4000-8000-000000000003', 'QA-05266-001', 'Banco de Sangre Guayacán (ficticio)',      'banco_de_sangre',      '05266', NULL),
  ('a1000000-0000-4000-8000-000000000004', 'QA-05615-001', 'Servicio Transfusional Orquídea (ficticio)', 'servicio_transfusion', '05615', NULL),
  ('a1000000-0000-4000-8000-000000000005', 'QA-05360-001', 'Servicio Transfusional Sietecueros (ficticio)', 'servicio_transfusion', '05360', NULL),
  ('a1000000-0000-4000-8000-000000000006', 'QA-11001-001', 'Banco de Sangre Sabana (ficticio)',        'banco_de_sangre',      '11001', NULL),
  ('a1000000-0000-4000-8000-000000000007', 'QA-11001-002', 'Banco de Sangre Monserrate (ficticio)',    'banco_de_sangre',      '11001', NULL),
  ('a1000000-0000-4000-8000-000000000008', 'QA-11001-003', 'Servicio Transfusional Chapinero (ficticio)', 'servicio_transfusion', '11001', NULL),
  ('a1000000-0000-4000-8000-000000000009', 'QA-11001-004', 'Banco de Sangre Humedal (ficticio)',       'banco_de_sangre',      '11001', NULL),
  ('a1000000-0000-4000-8000-000000000010', 'QA-76001-001', 'Banco de Sangre Farallones (ficticio)',    'banco_de_sangre',      '76001', NULL),
  ('a1000000-0000-4000-8000-000000000011', 'QA-76001-002', 'Servicio Transfusional Cristo Rey (ficticio)', 'servicio_transfusion', '76001', NULL),
  ('a1000000-0000-4000-8000-000000000012', 'QA-76520-001', 'Banco de Sangre Samán (ficticio)',         'banco_de_sangre',      '76520', NULL),
  ('a1000000-0000-4000-8000-000000000013', 'QA-08001-001', 'Banco de Sangre Arenosa (ficticio)',       'banco_de_sangre',      '08001', NULL),
  ('a1000000-0000-4000-8000-000000000014', 'QA-13001-001', 'Banco de Sangre Murallas (ficticio)',      'banco_de_sangre',      '13001', NULL),
  ('a1000000-0000-4000-8000-000000000015', 'QA-68001-001', 'Banco de Sangre Chicamocha (ficticio)',    'banco_de_sangre',      '68001', NULL),
  ('a1000000-0000-4000-8000-000000000016', 'QA-68276-001', 'Servicio Transfusional Lagos (ficticio)',  'servicio_transfusion', '68276', NULL),
  ('a1000000-0000-4000-8000-000000000017', 'QA-66001-001', 'Banco de Sangre Otún (ficticio)',          'banco_de_sangre',      '66001', NULL),
  ('a1000000-0000-4000-8000-000000000018', 'QA-17001-001', 'Banco de Sangre Nevado (ficticio)',        'banco_de_sangre',      '17001', NULL),
  ('a1000000-0000-4000-8000-000000000019', 'QA-52001-001', 'Banco de Sangre Galeras (ficticio)',       'banco_de_sangre',      '52001', NULL),
  ('a1000000-0000-4000-8000-000000000020', 'QA-54001-001', 'Banco de Sangre Zulia (ficticio)',         'banco_de_sangre',      '54001', NULL),
  ('a1000000-0000-4000-8000-000000000021', 'QA-50001-001', 'Servicio Transfusional Llanura (ficticio)', 'servicio_transfusion', '50001', NULL),
  ('a1000000-0000-4000-8000-000000000022', 'QA-91001-001', 'Banco de Sangre Amazonas (ficticio)',      'banco_de_sangre',      '91001', NULL)
) AS i(id, codigo, nombre, tipo, municipio, direccion)
JOIN territorio t ON t.codigo_divipola = i.municipio
ON CONFLICT (id) DO NOTHING;

-- Cada institución sembrada sale por Kafka (EV-07), como lo haría el servicio
-- al darla de alta: Campañas y Donación arman con eso su propia copia.
INSERT INTO evento_salida (id, tipo, version_esquema, tema, clave, carga, clave_natural, correlacion_id)
SELECT gen_random_uuid(), 'institution.changed', 1, 'redvital.institucional.institution-changed.v1', i.id::text,
       jsonb_build_object('institucion_id', i.id, 'codigo', i.codigo, 'nombre', i.nombre, 'tipo', i.tipo,
                          'territorio_codigo', t.codigo_divipola, 'territorio_ruta', t.ruta, 'vigente', i.vigente,
                          'cambiado_en', to_char(i.creado_en AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')),
       'semilla:' || i.id, 'semilla-qa'
FROM institucion i JOIN territorio t ON t.id = i.territorio_id
WHERE i.codigo LIKE 'QA-%'
ON CONFLICT (clave_natural) DO NOTHING;

COMMIT;
