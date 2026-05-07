import { Project, Node } from "ts-morph";
import * as fs from "fs";
import * as path from "path";

const MESSAGES_DIR = "messages";
const EN_PATH = path.join(MESSAGES_DIR, "en.json");

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

const project = new Project({ tsConfigFilePath: "tsconfig.json" });
const enMessages = JSON.parse(fs.readFileSync(EN_PATH, "utf-8"));
const enMap = getFlatKeys(enMessages);
const enKeys = Array.from(enMap.keys());

const files = project.getSourceFiles(["src/**/*.tsx", "src/**/*.ts"]);
const usedKeys = new Set<string>();

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
                                }
                            }
                        }
                    }
                });
            }
        }
    }
  });
}

const missingKeys = Array.from(usedKeys).filter(k => !enMap.has(k));
console.log(`Missing keys in en.json: ${missingKeys.length}`);
if (missingKeys.length > 0) {
    missingKeys.forEach(k => console.log(k));
}
