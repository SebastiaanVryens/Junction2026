-- M4: offline evaluation results shown on the public metrics page (factslens.eval.run --save-db).
CREATE TABLE eval_runs (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    name        text NOT NULL,
    result      jsonb NOT NULL
);
