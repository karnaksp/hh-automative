"""Browse utils"""

import os
import json
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException


def custom_wait(driver, timeout, condition_type, locator_tuple):
    wait = WebDriverWait(driver, timeout)
    return wait.until(condition_type(locator_tuple))


def eternal_wait(driver, timeout, condition_type, locator_tuple):
    while True:
        try:
            element = WebDriverWait(driver, timeout).until(
                condition_type(locator_tuple)
            )
            return element
        except TimeoutException:
            print(
                f"\n\nWaiting for the element(s) {locator_tuple} to become {condition_type}…"
            )
            time.sleep(0.5)
            continue


def load_data_from_json(path):
    return json.load(open(path, "r", encoding="utf-8"))


def save_data_to_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf-8"))


def add_cookies(cookies, driver):
    for cookie in cookies:
        driver.add_cookie(cookie)


def add_local_storage(local_storage, driver):
    keys = list(local_storage.keys())
    values = list(local_storage.values())
    for key, value in zip(keys, values):
        driver.execute_script(
            f"window.localStorage.setItem({json.dumps(key)}, {json.dumps(value)});"
        )


def get_first_folder(path):
    return os.path.normpath(path).split(os.sep)[0]


def delete_folder(folder_path):
    if os.path.exists(folder_path):
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)
            if os.path.isdir(file_path):
                delete_folder(file_path)
            else:
                os.remove(file_path)
        os.rmdir(folder_path)


def success(driver):
    try:
        custom_wait(
            driver,
            10,
            EC.presence_of_element_located,
            (By.XPATH, '//a[@data-qa="mainmenu_myResumes"]'),
        )
        return True
    except NoSuchElementException:
        return False


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


def login(login_page, driver, username, password):
    driver.get(login_page)
    show_more_button = eternal_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, '//button[@data-qa="expand-login-by-password"]'),
    )
    ACTION.click(show_more_button).perform()

    login_field = eternal_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, '//input[@data-qa="login-input-username"]'),
    )
    password_field = eternal_wait(
        driver, 10, EC.element_to_be_clickable, (By.XPATH, '//input[@type="password"]')
    )
    login_field.send_keys(username)
    password_field.send_keys(password)

    login_button = eternal_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, "//button[@data-qa='account-login-submit']"),
    )
    click_and_wait(login_button, 5)


def check_cookies_and_login(
    driver,
    login_page,
    cookies_path,
    local_storage_path,
    search_link,
    username,
    password,
):
    driver.get(login_page)
    if os.path.exists(cookies_path) and os.path.exists(local_storage_path):
        add_cookies(load_data_from_json(cookies_path), driver)
        add_local_storage(load_data_from_json(local_storage_path), driver)
        if navigate_and_check(search_link, driver):
            return
        else:
            delete_folder(get_first_folder(cookies_path))
    login(login_page, driver, username, password)
    navigate_and_check(search_link, driver)


def scroll_to_bottom(driver, delay=2.0):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
        time.sleep(delay)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if last_height == new_height:
            break
        last_height = new_height


def click_and_wait(element, delay=1.0):
    ACTION.move_to_element(element).click().perform()
    time.sleep(delay)


def js_click(driver, element):
    try:
        if element.is_displayed() and element.is_enabled():
            driver.execute_script(
                """
                arguments[0].scrollIntoView();
                var event = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true
                });
                arguments[0].dispatchEvent(event);
                """,
                element,
            )
        else:
            print("Element is not visible or not enabled for clicking.")
    except Exception as e:
        print(f"An error occurred: {e}")


def advanced_search(
    driver: webdriver,
) -> None:
    """
    Perform an advanced search using the given webdriver.

    Args:
        driver (webdriver): The webdriver to use for the advanced search.

    Returns:
        None
    """
    advanced_search_button = eternal_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, '//a[@data-qa="advanced-search"]'),
    )

    js_click(driver, advanced_search_button)
    advanced_search_textarea = eternal_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, '//input[@data-qa="vacancysearch__keywords-input"]'),
    )
    advanced_search_textarea.send_keys(JOB_SEARCH_QUERY)
    advanced_search_textarea.send_keys(Keys.TAB)

    if REGION == "global":
        clear_region(driver)

    try:
        exclude_these_results = custom_wait(
            driver,
            10,
            EC.element_to_be_clickable,
            (By.XPATH, '//input[@name="excluded_text"]'),
        )
        exclude_these_results.send_keys(EXCLUDE)
    except Exception:
        pass

    try:
        no_agency = custom_wait(
            driver,
            5,
            EC.element_to_be_clickable,
            (
                By.XPATH,
                '//input[@data-qa="advanced-search__label-item-label_not_from_agency"]',
            ),
        )
        js_click(driver, no_agency)
    except Exception:
        pass

    salary = custom_wait(
        driver,
        10,
        EC.element_to_be_clickable,
        (By.XPATH, '//input[@data-qa="advanced-search-salary"]'),
    )
    salary.send_keys(MIN_SALARY)

    if ONLY_WITH_SALARY:
        salary_only_checkbox = custom_wait(
            driver,
            10,
            EC.element_to_be_clickable,
            (By.XPATH, '//input[@name="only_with_salary"]'),
        )
        js_click(driver, salary_only_checkbox)

    quantity = driver.find_element(
        By.XPATH, '//input[@data-qa="advanced-search__items_on_page-item_100"]'
    )
    js_click(driver, quantity)

    advanced_search_submit_button = WAIT.until(
        EC.element_to_be_clickable(
            (By.XPATH, '//button[@data-qa="advanced-search-submit-button"]')
        )
    )
    js_click(driver, advanced_search_submit_button)
