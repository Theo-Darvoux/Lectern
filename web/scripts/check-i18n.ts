import { Project, Node } from "ts-morph";
import * as fs from "fs";
import * as path from "path";

const MESSAGES_DIR = "messages";
const EN_PATH = path.join(MESSAGES_DIR, "en.json");
const FR_PATH = path.join(MESSAGES_DIR, "fr.json");

function getFlatKeys(obj: Record<string, unknown>, prefix = ""): Map<string, string> {
  const keys = new Map<string, string>();
  for (const key in obj) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof obj[key] === "object" && obj[key] !== null && !Array.isArray(obj[key])) {
      const subKeys = getFlatKeys(obj[key] as Record<string, unknown>, fullKey);
      subKeys.forEach((v, k) => keys.set(k, v));
    } else if (typeof obj[key] === "string") {
      keys.set(fullKey, obj[key] as string);
    }
  }
  return keys;
}

function findDuplicates(filePath: string): string[] {
  const content = fs.readFileSync(filePath, "utf-8");
  const lines = content.split("\n");
  const duplicates: string[] = [];
  const stack: Set<string>[] = [new Set()];
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.includes("{")) stack.push(new Set());
    if (line.includes("}")) stack.pop();
    
    const match = line.match(/^"([^"]+)":/);
    if (match) {
      const key = match[1];
      const currentScope = stack[stack.length - 1];
      if (currentScope && currentScope.has(key)) {
        duplicates.push(`${filePath}:${i + 1} -> Duplicate key: "${key}"`);
      }
      currentScope?.add(key);
    }
  }
  return duplicates;
}

function extractPlaceholders(text: string): string[] {
  const placeholders = new Set<string>();
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === "{") {
      depth++;
      if (depth === 1) {
        let j = i + 1;
        let varName = "";
        while (j < text.length && text[j] !== "}" && text[j] !== "," && text[j] !== " ") {
          varName += text[j];
          j++;
        }
        if (varName && varName !== "#") placeholders.add(varName);
      }
    } else if (char === "}") {
      depth--;
    }
  }
  return Array.from(placeholders).sort();
}

const project = new Project({
  tsConfigFilePath: "tsconfig.json",
});

const enMessages = JSON.parse(fs.readFileSync(EN_PATH, "utf-8"));
const frMessages = JSON.parse(fs.readFileSync(FR_PATH, "utf-8"));

const enMap = getFlatKeys(enMessages);
const frMap = getFlatKeys(frMessages);
const enKeys = Array.from(enMap.keys());

console.log(`\n\x1b[1m--- 1. File Integrity ---\x1b[0m`);
const enDuplicates = findDuplicates(EN_PATH);
const frDuplicates = findDuplicates(FR_PATH);
if (enDuplicates.length > 0 || frDuplicates.length > 0) {
    [...enDuplicates, ...frDuplicates].forEach(d => console.error(`\x1b[31m❌ ${d}\x1b[0m`));
} else {
    console.log(`\x1b[32m✅ No duplicate keys found in raw files\x1b[0m`);
}

console.log(`\n\x1b[1m--- 2. Key Consistency ---\x1b[0m`);
const missingInFr = enKeys.filter(k => !frMap.has(k));
const extraInFr = Array.from(frMap.keys()).filter(k => !enMap.has(k));

if (missingInFr.length > 0) {
  console.error(`\x1b[31m❌ Missing in fr.json (${missingInFr.length}):\x1b[0m`);
  missingInFr.forEach(k => console.error(`  - ${k}`));
} else {
  console.log(`\x1b[32m✅ All English keys are present in French\x1b[0m`);
}

if (extraInFr.length > 0) {
  console.warn(`\x1b[33m⚠️ Extra keys in fr.json (${extraInFr.length}):\x1b[0m`);
  extraInFr.slice(0, 10).forEach(k => console.warn(`  - ${k}`));
}

console.log(`\n\x1b[1m--- 3. Placeholder Consistency ---\x1b[0m`);
let placeholderErrors = 0;
enMap.forEach((enVal, key) => {
    const frVal = frMap.get(key);
    if (frVal) {
        const enPlaceholders = extractPlaceholders(enVal);
        const frPlaceholders = extractPlaceholders(frVal);
        if (enPlaceholders.join(",") !== frPlaceholders.join(",")) {
            placeholderErrors++;
            console.error(`\x1b[31m❌ Mismatch in "${key}":\x1b[0m`);
            console.error(`   EN: ${enVal} (\x1b[36m${enPlaceholders.join(", ") || "none"}\x1b[0m)`);
            console.error(`   FR: ${frVal} (\x1b[36m${frPlaceholders.join(", ") || "none"}\x1b[0m)`);
        }
    }
});
if (placeholderErrors === 0) console.log(`\x1b[32m✅ All placeholders match between languages\x1b[0m`);

console.log(`\n\x1b[1m--- 4. Code Usage Scan ---\x1b[0m`);

const files = project.getSourceFiles(["src/**/*.tsx", "src/**/*.ts"]);
const usedKeys = new Set<string>();
const missingKeys = new Set<{ key: string, file: string, line: number }>();
const dynamicUsages: { file: string, line: number, text: string }[] = [];

function addUsedKey(key: string, file: string, line: number) {
    usedKeys.add(key);
    if (!enMap.has(key)) {
        missingKeys.add({ key, file, line });
    }
}

function protectNamespace(prefix: string) {
    if (!prefix) return;
    const cleanPrefix = prefix.endsWith(".") ? prefix.slice(0, -1) : prefix;
    enKeys.filter(k => k === cleanPrefix || k.startsWith(cleanPrefix + ".")).forEach(k => usedKeys.add(k));
}

for (const file of files) {
  const filePath = file.getFilePath().replace(process.cwd(), "");
  
  file.forEachDescendant((node) => {
    // 1. Check for useTranslations namespaces
    if (Node.isVariableDeclaration(node)) {
        const initializer = node.getInitializer();
        if (initializer && Node.isCallExpression(initializer)) {
            const callName = initializer.getExpression().getText();
            if (callName === "useTranslations") {
                const args = initializer.getArguments();
                const namespace = args.length > 0 && (Node.isStringLiteral(args[0]) || Node.isNoSubstitutionTemplateLiteral(args[0])) 
                    ? args[0].getLiteralValue() 
                    : null;
                
                const tVarName = node.getName();
                
                // Track usages of this 't' variable
                file.forEachDescendant((child) => {
                    if (Node.isCallExpression(child)) {
                        const expr = child.getExpression();
                        if (expr.getText() === tVarName || (Node.isPropertyAccessExpression(expr) && expr.getExpression().getText() === tVarName)) {
                            const tArgs = child.getArguments();
                            if (tArgs.length > 0) {
                                const arg = tArgs[0];
                                const line = child.getStartLineNumber();
                                if (Node.isStringLiteral(arg) || Node.isNoSubstitutionTemplateLiteral(arg)) {
                                    const subKey = arg.getLiteralValue();
                                    addUsedKey(namespace ? `${namespace}.${subKey}` : subKey, filePath, line);
                                } else if (Node.isTemplateExpression(arg)) {
                                    const head = arg.getHead().getLiteralText();
                                    protectNamespace(namespace ? `${namespace}.${head}` : head);
                                    dynamicUsages.push({ file: filePath, line: line, text: child.getText() });
                                } else {
                                    if (namespace) protectNamespace(namespace);
                                    dynamicUsages.push({ file: filePath, line: line, text: child.getText() });
                                }
                            }
                        }
                    }
                });
            }
        }
    }
    
    /* Global t() check removed to avoid false positives with local translators */


    // 3. Special case for _onStatusUpdate wrapper in upload-client.ts
    if (Node.isCallExpression(node) && node.getExpression().getText() === "_onStatusUpdate") {
        const args = node.getArguments();
        if (args.length >= 2 && Node.isStringLiteral(args[1])) {
            addUsedKey(`Upload.${args[1].getLiteralValue()}`, filePath, node.getStartLineNumber());
        }
    }
  });
}

// 4. Hardcoded exceptions for dynamically passed t() functions
protectNamespace("AutoTitle");

const trulyUnused = enKeys.filter(k => !usedKeys.has(k));
if (trulyUnused.length > 0) {
    console.warn(`\n⚠️ Unused keys in en.json (${trulyUnused.length}):`);
    trulyUnused.slice(0, 20).forEach(k => console.warn(`  - ${k}`));
    if (trulyUnused.length > 20) console.warn(`  ... and ${trulyUnused.length - 20} more`);
} else {
    console.log(`\x1b[32m✅ All keys in en.json are used (based on static analysis)\x1b[0m`);
}

if (missingKeys.size > 0) {
    console.error(`\n❌ Missing translations found (${missingKeys.size}):`);
    Array.from(missingKeys).slice(0, 20).forEach(m => console.error(`  - ${m.file}:${m.line} -> Missing "${m.key}"`));
    if (missingKeys.size > 20) console.error(`  ... and ${missingKeys.size - 20} more`);
} else {
    console.log(`\x1b[32m✅ No missing static translations detected\x1b[0m`);
}

if (dynamicUsages.length > 0) {
    console.log(`\nℹ️ Found ${dynamicUsages.length} dynamic usages that couldn't be statically analyzed:`);
    dynamicUsages.slice(0, 5).forEach(u => console.log(`  - ${u.file}:${u.line} -> ${u.text}`));
}


console.log(`\nScan complete.`);
