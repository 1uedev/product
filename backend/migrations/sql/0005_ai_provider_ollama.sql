-- 0005: allow the local Ollama adapter as AI provider in the run log.
-- The CHECK constraint was created unnamed in 0002 and therefore carries the generated name ai_runs_provider_check.
ALTER TABLE app.ai_runs DROP CONSTRAINT ai_runs_provider_check;
ALTER TABLE app.ai_runs ADD CONSTRAINT ai_runs_provider_check CHECK (provider IN ('mock', 'anthropic', 'ollama'));
