"""Login utils"""


def navigate_and_check(probe_page, driver):
    driver.get(probe_page)
    time.sleep(5)
    if success(driver):
        save_data_to_json(driver.get_cookies(), COOKIES_PATH)
        save_data_to_json(
            {
                key: driver.execute_script(
                    f"return window.localStorage.getItem('{key}');"
                )
                for key in driver.execute_script(
                    "return Object.keys(window.localStorage);"
                )
            },
            LOCAL_STORAGE_PATH,
        )
        return True
    else:
        return False
