1267
1268
1269
1270
1271
1272
1273
1274
1275
1276
1277
1278
1279
1280
1281
1282
1283
1284
1285
1286
1287
1288
1289
1290
1291
1292
1293
1294
1295
1296
1297
1298
1299
1300
1301
1302
1303
1304
1305
1306
1307
1308
1309
1310
1311
1312
1313
1314
1315
1316
# =====================================================================
                    check_results(history)
            
            # Прогноз по времени
            if current_time - last_check_time >= CHECK_INTERVAL:
                check_and_predict()
                last_check_time = current_time
            
            # Проверка результатов
            history = load_history()
            for entry in history:
                if entry.get("status") == "pending":
                    check_results(history)
            
            # Обучение / переобучение ML
            if current_time - last_train_time >= TRAIN_EVERY:
                data_count = len(load_data())
                if data_count >= MIN_TRAIN_SAMPLES + 1:
                    train_ml_model()
                else:
                    print(
                        f"⏳ ML: собираем данные "
                        f"{data_count}/{MIN_TRAIN_SAMPLES + 1}",
                        flush=True
                    )
                last_train_time = current_time
            
            # Статистика
            if current_time - last_stats_time > 3600:
                send_stats_report()
                last_stats_time = current_time
            
            # Очистка
            if len(processed_games) > 500:
                processed_games.clear()
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("🛑 Бот остановлен", flush=True)
            data_count = len(load_data())
            print(f"📊 Всего собрано записей: {data_count}", flush=True)
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(30)

if __name__ == "__main__":
    main()