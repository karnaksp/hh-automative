"""Utils for searching"""


def clear_region(driver):
    try:
        clear_region_buttons = custom_wait(
            driver,
            10,
            EC.presence_of_all_elements_located,
            (By.XPATH, '//button[@data-qa="bloko-tag__cross"]'),
        )
        for button in clear_region_buttons:
            js_click(driver, button)
    except Exception:
        return


def select_all_countries(driver):
    region_select_button = WAIT.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//button[@data-qa="advanced-search-region-selectFromList"]')
        )
    )
    region_select_button.click()
    countries = driver.find_elements(
        By.XPATH, '//input[@name="bloko-tree-selector-default-name-0"]'
    )
    for country in countries:
        country.click()
    region_submit_button = WAIT.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//button[@data-qa="bloko-tree-selector-popup-submit"]')
        )
    )
    region_submit_button.click()


def international_ok(driver):
    try:
        international = WAIT.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//button[@data-qa="relocation-warning-confirm"]')
            )
        )
        international.click()
    except TimeoutException:
        pass
    driver.refresh()
