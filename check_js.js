const fs = require('fs');
const html = fs.readFileSync('c:/Users/dawit/Desktop/Liyu Kids Mart/app/templates/mini_app/index.html', 'utf8');

const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (scriptMatch) {
  const jsCode = scriptMatch[1];
  try {
    const vm = require('vm');
    new vm.Script(jsCode);
    console.log("No syntax errors found in the main script tag.");
  } catch (e) {
    console.error("Syntax Error in script:", e);
  }
} else {
  console.log("No script tag found");
}
