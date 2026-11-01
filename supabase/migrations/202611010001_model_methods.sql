-- Allow the adopted model methods in published indicators (2026-11-01).
-- model: road/rail noise model (NOISE_MODEL=road), pixel_overlay_poi: L1 light redistribution (LIGHT_MODEL=poi).
-- Existing values stay valid; only the allowed list grows.
ALTER TABLE public.cell_indicators DROP CONSTRAINT IF EXISTS cell_indicators_method_check;
ALTER TABLE public.cell_indicators ADD CONSTRAINT cell_indicators_method_check
    CHECK (method IN ('direct', 'idw', 'areal_weighting', 'pixel_overlay', 'pixel_overlay_poi', 'model'));
