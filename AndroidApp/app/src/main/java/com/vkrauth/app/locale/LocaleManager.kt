package com.vkrauth.app.locale

import android.content.Context
import android.content.res.Configuration
import androidx.annotation.StringRes
import com.vkrauth.app.R
import java.util.Locale

/**
 * Поддерживаемые языки приложения. Ровно две локали (см. res/values = en,
 * res/values-ru = ru). [tag] — BCP-47 язык, [labelRes] — название языка на нём
 * самом (English / Русский) для меню выбора.
 */
enum class AppLanguage(val tag: String, @StringRes val labelRes: Int) {
    ENGLISH("en", R.string.language_english),
    RUSSIAN("ru", R.string.language_russian);

    companion object {
        fun fromTag(tag: String?): AppLanguage =
            entries.firstOrNull { it.tag == tag } ?: ENGLISH
    }
}

/**
 * Применение и хранение выбранной локали без AppCompat.
 *
 * Приложение — чистый Compose на [androidx.activity.ComponentActivity] с
 * minSdk 26, поэтому AppCompatDelegate.setApplicationLocales ненадёжен ниже
 * API 33. Вместо этого выбранный язык хранится в SharedPreferences (синхронное
 * чтение нужно прямо в attachBaseContext) и применяется обёрткой Configuration.
 *
 * Поток смены языка: [persist] → Activity.recreate() → attachBaseContext снова
 * вызывает [wrap], который читает новое значение и пересоздаёт Configuration.
 */
object LocaleManager {
    private const val PREFS = "scud_locale_prefs"
    private const val KEY_LANG = "app_language"

    /** Текущий выбор. Первый запуск следует языку устройства (ru → RU, иначе EN). */
    fun current(context: Context): AppLanguage {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val tag = prefs.getString(KEY_LANG, null)
        if (tag != null) return AppLanguage.fromTag(tag)
        return if (deviceLanguage(context) == AppLanguage.RUSSIAN.tag) {
            AppLanguage.RUSSIAN
        } else {
            AppLanguage.ENGLISH
        }
    }

    /** Сохраняет выбор. apply() обновляет in-memory значение синхронно, так что
     *  следующий [current]/[wrap] (после recreate) увидит его сразу. */
    fun persist(context: Context, language: AppLanguage) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_LANG, language.tag)
            .apply()
    }

    /** Оборачивает базовый контекст в Configuration с выбранной локалью.
     *  Вызывается из attachBaseContext в MainActivity и ScudApplication. */
    fun wrap(base: Context): Context {
        val language = current(base)
        val locale = Locale(language.tag)
        Locale.setDefault(locale)
        val config = Configuration(base.resources.configuration)
        config.setLocale(locale)
        return base.createConfigurationContext(config)
    }

    /** Язык устройства (minSdk 26 → locales[0] всегда доступен). */
    private fun deviceLanguage(context: Context): String =
        context.resources.configuration.locales[0].language
}
