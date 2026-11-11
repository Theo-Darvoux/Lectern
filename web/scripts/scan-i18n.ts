import { Project, Node } from "ts-morph";
import * as fs from "fs";

const project = new Project({
  tsConfigFilePath: "tsconfig.json",
});

const files = project.getSourceFiles(["src/app/**/*.tsx", "src/components/**/*.tsx"]);
let totalStringsFound = 0;

// Ignore empty space, pure numbers, and basic punctuation
const IGNORED_TEXT = /^[\s\n\r]*$|^[0-9]+$|^[.,;:!?\-\+\/\*\(\)\[\]\{\}\s]+$|^[|]+$|^(left|right|top|bottom|horizontal|vertical|center|system|default|start|end|icon|sm|md|lg|xl|2xl|outline|ghost|link|destructive|secondary|primary)$/i;

// Attributes that typically contain user-facing text
const TEXT_ATTRIBUTES = ["placeholder", "alt", "title", "aria-label", "label", "description", "fallback"];

const report: any[] = [];

for (const file of files) {
  const filePath = file.getFilePath();
  // ignore api routes or purely server files without jsx, but we only globbed .tsx anyway
  const stringsInFile: { line: number, text: string, type: string }[] = [];

  file.forEachDescendant((node) => {
    // 1. Check direct JSX Text
    if (Node.isJsxText(node)) {
      const text = node.getText().replace(/[\r\n]+/g, " ").trim();
      if (text && !IGNORED_TEXT.test(text)) {
         stringsInFile.push({ line: node.getStartLineNumber(), text, type: "JsxText" });
      }
    } 
    // 2. Check String Literals
    else if (Node.isStringLiteral(node)) {
      const parent = node.getParent();
      
      // 2a. String Literals in Attributes (e.g. placeholder="Search...")
      if (Node.isJsxAttribute(parent)) {
        const attrName = parent.getNameNode().getText();
        if (TEXT_ATTRIBUTES.includes(attrName)) {
           const text = node.getLiteralValue().trim();
           if (text && !IGNORED_TEXT.test(text)) {
              stringsInFile.push({ line: node.getStartLineNumber(), text, type: `Attribute: ${attrName}` });
           }
        }
      } 
      // 2b. String Literals inside JSX Expressions (e.g. <div>{"Hello"}</div>)
      else if (Node.isJsxExpression(parent)) {
        const grandParent = parent.getParent();
        if (Node.isJsxElement(grandParent) || Node.isJsxFragment(grandParent)) {
            const text = node.getLiteralValue().trim();
            if (text && !IGNORED_TEXT.test(text)) {
                stringsInFile.push({ line: node.getStartLineNumber(), text, type: "JsxExpression StringLiteral" });
            }
        }
      }
      // 2c. String Literals as default values in function parameters (often default props)
      else if (Node.isBindingElement(parent)) {
         const text = node.getLiteralValue().trim();
         if (text && !IGNORED_TEXT.test(text)) {
             stringsInFile.push({ line: node.getStartLineNumber(), text, type: "BindingElement Default (Prop)" });
         }
      }
    }
  });

  if (stringsInFile.length > 0) {
    report.push({
      file: filePath.replace(project.getCompilerOptions().rootDir || process.cwd(), ""),
      strings: stringsInFile
    });
    totalStringsFound += stringsInFile.length;
  }
}

console.log(`Found ${totalStringsFound} hardcoded strings in ${report.length} files.`);
fs.writeFileSync("i18n-report.json", JSON.stringify(report, null, 2));
