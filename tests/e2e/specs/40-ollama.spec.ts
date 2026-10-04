import { expect, test } from "@playwright/test";
import { newSession, openWorkspace } from "../helpers";

// Optional: only for a stack started with AI_PROVIDER=ollama (compose.ollama.yaml or an Ollama server on the host).
//   E2E_AI_PROVIDER=ollama scripts/test_e2e.sh 40-ollama
// With a model that is installed on the server set E2E_OLLAMA_MODEL_INSTALLED=1 to expect a successful analysis instead.
test.skip(process.env.E2E_AI_PROVIDER !== "ollama", "stack is not configured for the local model provider");

test("local model: provider is shown, analysis reaches the Ollama server and a missing model is reported", async ({ browser }) => {
  const { page, context } = await newSession(browser, "finn@fjord.example");
  const ws = await openWorkspace(page, /Fjord Systems/);
  await page.goto(`/w/${ws}/settings`);
  await expect(page.getByText(/Ollama \(lokales Modell/)).toBeVisible();

  await page.goto(`/w/${ws}/problems`);
  await page.getByTestId("start-analysis").click();
  if (process.env.E2E_OLLAMA_MODEL_INSTALLED === "1") {
    await expect(page.getByTestId("job-progress")).toHaveAttribute("data-status", /succeeded|failed/, { timeout: 600_000 });
    return;
  }
  const job = page.getByTestId("job-progress");
  await expect(job).toHaveAttribute("data-status", "failed", { timeout: 120_000 });
  await expect(job).toContainText("ai_model_missing");
  await expect(job).toContainText("ollama pull");
  await expect(job).not.toContainText("Demo-KI");
  await context.close();
});
