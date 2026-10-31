-- Engine-owned tables. Apply with python -m app.db.migrate using a privileged
-- direct database connection. Supabase REST/anon clients cannot read these data.
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
-- Explicit type/function schemas: migration does not depend on search_path.

CREATE TABLE public.sensors (
    sensor_id text PRIMARY KEY,
    source text NOT NULL CHECK (source = 'sdot'),
    geom extensions.geometry(Point, 4326) NOT NULL,
    name text, gu_name text, dong_code text, installed_at timestamptz,
    location_status text NOT NULL CHECK (location_status IN ('observed', 'estimated'))
);
CREATE INDEX sensors_geom_gist ON public.sensors USING gist (geom);
CREATE TABLE public.sdot_observations (
    sensor_id text NOT NULL REFERENCES public.sensors ON DELETE CASCADE,
    observed_at timestamptz NOT NULL,
    noise_db double precision, illuminance_lux double precision,
    sample_count integer NOT NULL CHECK (sample_count > 0), quality_flag text,
    PRIMARY KEY (sensor_id, observed_at)
);
CREATE INDEX sdot_observations_time_idx ON public.sdot_observations (observed_at);
CREATE TABLE public.admin_dong (
    dong_code text PRIMARY KEY, dong_name text, gu_name text,
    geom extensions.geometry(MultiPolygon, 4326) NOT NULL,
    CHECK (extensions.ST_IsValid(geom))
);
CREATE INDEX admin_dong_geom_gist ON public.admin_dong USING gist (geom);
CREATE TABLE public.living_population (
    dong_code text NOT NULL, stat_date date NOT NULL,
    hour smallint NOT NULL CHECK (hour BETWEEN 0 AND 23),
    total_population double precision NOT NULL CHECK (total_population >= 0),
    dong_name text, area_km2 double precision CHECK (area_km2 > 0),
    PRIMARY KEY (dong_code, stat_date, hour)
);
CREATE INDEX living_population_time_idx ON public.living_population (stat_date, hour);
CREATE TABLE public.night_lights (
    pixel_id text NOT NULL, product text NOT NULL,
    period_start date NOT NULL, period_end date NOT NULL,
    geom extensions.geometry(Polygon, 4326) NOT NULL,
    pixel_size_deg double precision NOT NULL CHECK (pixel_size_deg > 0),
    radiance double precision, quality_flag integer, cloud_flag integer,
    PRIMARY KEY (pixel_id, product, period_start, period_end),
    CHECK (period_end >= period_start)
);
CREATE INDEX night_lights_geom_gist ON public.night_lights USING gist (geom);
CREATE INDEX night_lights_period_idx ON public.night_lights (period_end);
CREATE TABLE public.grid_cells (
    grid_id text PRIMARY KEY, ix integer NOT NULL, iy integer NOT NULL,
    geom extensions.geometry(Polygon, 4326) NOT NULL,
    geom_5179 extensions.geometry(Polygon, 5179) NOT NULL,
    UNIQUE (ix, iy)
);
CREATE INDEX grid_cells_geom_gist ON public.grid_cells USING gist (geom);
CREATE INDEX grid_cells_geom_5179_gist ON public.grid_cells USING gist (geom_5179);
CREATE TABLE public.cell_indicators (
    grid_id text NOT NULL REFERENCES public.grid_cells ON DELETE CASCADE,
    time_slot text NOT NULL CHECK (time_slot IN ('day', 'evening', 'night', 'dawn')),
    indicator text NOT NULL CHECK (indicator IN ('noise', 'light', 'crowd')),
    raw_value double precision, raw_unit text,
    status text NOT NULL CHECK (status IN ('observed', 'estimated', 'missing', 'not_applicable')),
    confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    method text CHECK (method IN ('direct', 'idw', 'areal_weighting', 'pixel_overlay')),
    source text CHECK (source IN ('sdot', 'living_population', 'nasa_black_marble')),
    observed_at timestamptz, period_start timestamptz, period_end timestamptz,
    evidence jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(evidence) = 'array'), note text,
    PRIMARY KEY (grid_id, time_slot, indicator),
    CHECK ((raw_value IS NULL AND status IN ('missing', 'not_applicable')) OR
           (raw_value IS NOT NULL AND status IN ('observed', 'estimated'))),
    CHECK (indicator <> 'light' OR time_slot <> 'day' OR status = 'not_applicable')
);
CREATE INDEX cell_indicators_slot_grid_idx ON public.cell_indicators (time_slot, grid_id);
CREATE TABLE public.source_metadata (
    source_id text PRIMARY KEY, name text NOT NULL,
    status text NOT NULL CHECK (status IN ('ready', 'partial', 'not_loaded', 'error')),
    last_updated_at timestamptz, observation_count bigint NOT NULL CHECK (observation_count >= 0),
    missing_rate double precision CHECK (missing_rate BETWEEN 0 AND 1),
    coverage_bbox jsonb, coverage_description text,
    temporal_start timestamptz, temporal_end timestamptz,
    spatial_resolution text, update_frequency text, notes text
);
-- Compact per-sensor means used for click-relative evidence; no raw reprocessing.
CREATE TABLE public.sensor_summaries (
    time_slot text NOT NULL, indicator text NOT NULL,
    sensor_id text NOT NULL REFERENCES public.sensors ON DELETE CASCADE,
    payload jsonb NOT NULL, PRIMARY KEY (time_slot, indicator, sensor_id)
);
CREATE TABLE public.engine_dataset (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    format_version integer NOT NULL, published_at timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['sensors', 'sdot_observations', 'admin_dong',
        'living_population', 'night_lights', 'grid_cells', 'cell_indicators',
        'source_metadata', 'sensor_summaries', 'engine_dataset']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', t);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
            EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', t);
        END IF;
    END LOOP;
END $$;
-- No anon/authenticated RLS policies. Server needs owner/BYPASSRLS credentials.
