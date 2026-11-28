import {setTimeout as delay} from "node:timers/promises";
import {connectPage} from "./chromium.mjs";

const [portFile, url] = process.argv.slice(2);
const deadline = Date.now() + 20_000;
const {command, diagnostics, close} = await connectPage(portFile);
try {
  await command("Page.navigate", {url});
  const expression = "document.querySelector('main#content:not([aria-busy=true])') && document.documentElement.outerHTML";
  for (;;) {
    try {
      const {result} = await command("Runtime.evaluate", {expression, returnByValue:true});
      if (result.value) {
        process.stdout.write(result.value);
        break;
      }
    } catch (error) {
      if (!error.message.includes("context")) throw error;
    }
    if (Date.now() >= deadline) throw Error("Page did not finish rendering: " + url + "\n" + diagnostics.join("\n"));
    await delay(40);
  }
} finally {
  await close();
}
