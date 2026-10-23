-- RedVital · QA · campañas sintéticas (db_campana). Nunca en producción.
--
-- Instituciones fijas, las mismas de las cuentas de Identidad:
--   a1000000-…-0001 Banco Aurora (Medellín, 05001)
--   a1000000-…-0002 Banco Ceiba  (Bello, 05088)
-- Las fechas son relativas al momento de aplicar la semilla, para que QA
-- siempre tenga jornadas vigentes. Idempotente: puede aplicarse de nuevo.
--
-- Cada campaña publicada deja además su EV-03 (campaign.published) en la
-- bandeja de salida, tal como lo haría el servicio al publicarla: el
-- publicador lo envía a Kafka y Donación construye su proyección local. Sin
-- esto, Donación no podría confirmar donaciones en estas jornadas.

BEGIN;

INSERT INTO campania (id, institucion_id, territorio_codigo, territorio_ruta, nombre, descripcion, sede,
                      inicia_en, termina_en, cupo_total, estado, publicada_en, creada_por)
VALUES
  ('d4000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', '05001', '/00/05/05001',
   'Jornada Ciudad Universitaria', 'Donación de sangre total abierta a la comunidad universitaria.',
   'Bloque 22, Ciudad Universitaria, Medellín',
   date_trunc('day', now()) - interval '1 day' + interval '13 hours',
   date_trunc('day', now()) + interval '5 days' + interval '22 hours', 120, 'publicada', now() - interval '2 days',
   'b2000000-0000-4000-8000-000000000002'),
  ('d4000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000001', '05001', '/00/05/05001',
   'Jornada Parque Explora', NULL, 'Plazoleta del Parque Explora, Medellín',
   date_trunc('day', now()) + interval '7 days' + interval '13 hours',
   date_trunc('day', now()) + interval '8 days' + interval '22 hours', 80, 'publicada', now() - interval '1 day',
   'b2000000-0000-4000-8000-000000000002'),
  ('d4000000-0000-4000-8000-000000000003', 'a1000000-0000-4000-8000-000000000001', '05001', '/00/05/05001',
   'Jornada Centro Administrativo La Alpujarra', 'Pendiente de publicar: sirve para probar la publicación y EV-03.',
   'Centro Administrativo La Alpujarra, Medellín',
   date_trunc('day', now()) + interval '14 days' + interval '13 hours',
   date_trunc('day', now()) + interval '15 days' + interval '22 hours', 60, 'borrador', NULL,
   'b2000000-0000-4000-8000-000000000002'),
  ('d4000000-0000-4000-8000-000000000004', 'a1000000-0000-4000-8000-000000000002', '05088', '/00/05/05088',
   'Jornada Parque de Bello', NULL, 'Parque Santander, Bello',
   date_trunc('day', now()) - interval '1 day' + interval '13 hours',
   date_trunc('day', now()) + interval '3 days' + interval '22 hours', NULL, 'publicada', now() - interval '3 days',
   'b2000000-0000-4000-8000-000000000002'),
  ('d4000000-0000-4000-8000-000000000005', 'a1000000-0000-4000-8000-000000000001', '05001', '/00/05/05001',
   'Jornada Estación San Antonio', NULL, 'Estación San Antonio del Metro, Medellín',
   date_trunc('day', now()) - interval '20 days' + interval '13 hours',
   date_trunc('day', now()) - interval '18 days' + interval '22 hours', 100, 'cerrada', now() - interval '25 days',
   'b2000000-0000-4000-8000-000000000002')
ON CONFLICT (id) DO NOTHING;

-- EV-03 de las campañas publicadas o cerradas (una cerrada también se publicó).
INSERT INTO evento_salida (id, tipo, version_esquema, tema, clave, carga, clave_natural, correlacion_id)
SELECT gen_random_uuid(), 'campaign.published', 1, 'redvital.campanias.campaign-published.v1', c.id::text,
       jsonb_build_object(
         'campania_id', c.id,
         'institucion_id', c.institucion_id,
         'nombre', c.nombre,
         'sede', c.sede,
         'territorio_codigo', c.territorio_codigo,
         'territorio_ruta', c.territorio_ruta,
         'inicia_en', to_char(c.inicia_en AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
         'termina_en', to_char(c.termina_en AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
         'publicada_en', to_char(c.publicada_en AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
       'campania_publicada:' || c.id, 'semilla-qa'
FROM campania c
WHERE c.id::text LIKE 'd4000000-%' AND c.estado IN ('publicada', 'cerrada')
ON CONFLICT (clave_natural) DO NOTHING;

COMMIT;
