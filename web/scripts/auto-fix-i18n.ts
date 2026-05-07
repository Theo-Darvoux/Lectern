import { Project, Node } from "ts-morph";
import * as fs from "fs";
import * as path from "path";

const MESSAGES_DIR = "messages";
const EN_PATH = path.join(MESSAGES_DIR, "en.json");
const FR_PATH = path.join(MESSAGES_DIR, "fr.json");

function getFlatKeys(obj: any, prefix = ""): Map<string, string> {
  const keys = new Map<string, string>();
  for (const key in obj) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof obj[key] === "object" && obj[key] !== null && !Array.isArray(obj[key])) {
      const subKeys = getFlatKeys(obj[key], fullKey);
      subKeys.forEach((v, k) => keys.set(k, v));
    } else if (typeof obj[key] === "string") {
      keys.set(fullKey, obj[key] as string);
    }
  }
  return keys;
}

function setNestedValue(obj: any, pathStr: string, value: any) {
  const parts = pathStr.split('.');
  let current = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof current[parts[i]] !== "object" || current[parts[i]] === null) {
        current[parts[i]] = {}; // Overwrite if it was a string or undefined
    }
    current = current[parts[i]];
  }
  current[parts[parts.length - 1]] = value;
}

function deleteNestedValue(obj: any, pathStr: string) {
  const parts = pathStr.split('.');
  let current = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof current[parts[i]] !== "object" || current[parts[i]] === null) return;
    current = current[parts[i]];
  }
  delete current[parts[parts.length - 1]];
  
  // Cleanup empty objects
  for (let i = parts.length - 2; i >= 0; i--) {
      let c = obj;
      for(let j=0; j<i; j++) c = c[parts[j]];
      if (Object.keys(c[parts[i]]).length === 0) delete c[parts[i]];
  }
}

const project = new Project({ tsConfigFilePath: "tsconfig.json" });
const files = project.getSourceFiles(["src/**/*.tsx", "src/**/*.ts"]);
const usedKeys = new Set<string>();

const enMessages = JSON.parse(fs.readFileSync(EN_PATH, "utf-8"));
const frMessages = JSON.parse(fs.readFileSync(FR_PATH, "utf-8"));

// 1. Manual migrations first
function migrate(sourcePath: string, targetPath: string) {
    const enFlat = getFlatKeys(enMessages);
    const frFlat = getFlatKeys(frMessages);
    
    if (enFlat.has(sourcePath) && !enFlat.has(targetPath)) {
        setNestedValue(enMessages, targetPath, enFlat.get(sourcePath));
    }
    if (frFlat.has(sourcePath) && !frFlat.has(targetPath)) {
        setNestedValue(frMessages, targetPath, frFlat.get(sourcePath));
    }
}

const enFlatInit = getFlatKeys(enMessages);
for (const key of enFlatInit.keys()) {
    if (key.startsWith("UploadDrawer.")) {
        migrate(key, key.replace("UploadDrawer.", "Upload."));
    }
    if (key.startsWith("Admin.Dashboard.") && !key.includes("reconciliation") && !key.includes("services.common")) {
        migrate(key, key.replace("Admin.Dashboard.", "Admin.Dashboard.reconciliation."));
        migrate(key, key.replace("Admin.Dashboard.", "Admin.Dashboard.services.common."));
    }
}

function protectNamespace(prefix: string) {
    if (!prefix) return;
    const cleanPrefix = prefix.endsWith(".") ? prefix.slice(0, -1) : prefix;
    const currentEnFlat = getFlatKeys(enMessages);
    Array.from(currentEnFlat.keys()).filter(k => k === cleanPrefix || k.startsWith(cleanPrefix + ".")).forEach(k => usedKeys.add(k));
}

for (const file of files) {
  file.forEachDescendant((node) => {
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
                file.forEachDescendant((child) => {
                    if (Node.isCallExpression(child)) {
                        const expr = child.getExpression();
                        if (expr.getText() === tVarName || (Node.isPropertyAccessExpression(expr) && expr.getExpression().getText() === tVarName)) {
                            const tArgs = child.getArguments();
                            if (tArgs.length > 0) {
                                const arg = tArgs[0];
                                if (Node.isStringLiteral(arg) || Node.isNoSubstitutionTemplateLiteral(arg)) {
                                    const subKey = arg.getLiteralValue();
                                    usedKeys.add(namespace ? `${namespace}.${subKey}` : subKey);
                                } else if (Node.isTemplateExpression(arg)) {
                                    const head = arg.getHead().getLiteralText();
                                    protectNamespace(namespace ? `${namespace}.${head}` : head);
                                } else {
                                    if (namespace) protectNamespace(namespace);
                                }
                            }
                        }
                    }
                });
            }
        }
    }
    
    // Global t()
    if (Node.isCallExpression(node) && node.getExpression().getText() === "t") {
        const args = node.getArguments();
        if (args.length > 0) {
            const arg = args[0];
            if (Node.isStringLiteral(arg) || Node.isNoSubstitutionTemplateLiteral(arg)) {
                usedKeys.add(arg.getLiteralValue());
            } else if (Node.isTemplateExpression(arg)) {
                protectNamespace(arg.getHead().getLiteralText());
            } else if (Node.isBinaryExpression(arg)) {
                const left = arg.getLeft();
                if (Node.isStringLiteral(left)) protectNamespace(left.getLiteralValue());
            }
        }
    }

    // Special case for _onStatusUpdate wrapper in upload-client.ts
    if (Node.isCallExpression(node) && node.getExpression().getText() === "_onStatusUpdate") {
        const args = node.getArguments();
        if (args.length >= 2 && Node.isStringLiteral(args[1])) {
            usedKeys.add(`Upload.${args[1].getLiteralValue()}`);
        }
    }
  });
}

// 1.5 Hardcoded exceptions for dynamically passed t() functions
protectNamespace("AutoTitle");

const currentEnFlat = getFlatKeys(enMessages);

// 2. Add missing keys
const missingKeys = Array.from(usedKeys).filter(k => !currentEnFlat.has(k));
for (const key of missingKeys) {
    const parts = key.split('.');
    const last = parts[parts.length - 1];
    setNestedValue(enMessages, key, last);
    setNestedValue(frMessages, key, last);
}

// 3. Remove unused keys
const newEnFlat = getFlatKeys(enMessages);
const unusedKeys = Array.from(newEnFlat.keys()).filter(k => !usedKeys.has(k));
for (const key of unusedKeys) {
    deleteNestedValue(enMessages, key);
    deleteNestedValue(frMessages, key);
}

// Format properly
fs.writeFileSync(EN_PATH, JSON.stringify(enMessages, null, 2) + "\n", "utf-8");
fs.writeFileSync(FR_PATH, JSON.stringify(frMessages, null, 2) + "\n", "utf-8");

console.log(`Added ${missingKeys.length} missing keys.`);
console.log(`Removed ${unusedKeys.length} unused keys.`);
