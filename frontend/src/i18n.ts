import i18n from "i18next";
import { initReactI18next } from "react-i18next";

export const resources = {
  en: { translation: { appName: "MateScope", tagline: "A clear view of your TeslaMate data.", shellReady: "The application shell is ready.", apiStatus: "API status", apiOnline: "Connected", apiOffline: "Unavailable", language: "中文", next: "The onboarding and data flows arrive in later milestones." } },
  zh: { translation: { appName: "MateScope", tagline: "清晰查看 TeslaMate 数据。", shellReady: "应用外壳已就绪。", apiStatus: "API 状态", apiOnline: "已连接", apiOffline: "不可用", language: "English", next: "引导与数据流程将在后续里程碑中加入。" } },
} as const;

function updateDocumentLanguage(language: string) {
  document.documentElement.lang = language.startsWith("zh") ? "zh" : "en";
}

updateDocumentLanguage("en");
i18n.on("languageChanged", updateDocumentLanguage);
void i18n.use(initReactI18next).init({ resources, lng: "en", fallbackLng: "en", interpolation: { escapeValue: false } });
export default i18n;
