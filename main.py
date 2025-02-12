"""Main logic of bot"""

from utils.browser_utils import (
    check_cookies_and_login,
    advanced_search,
    click_all_jobs_on_the_page,
)
from utils.login_utils import navigate_and_check
from utils.search_utils import clear_region, select_all_countries, international_ok
from utils.resume_utils import (
    fill_in_cover_letter,
    check_cover_letter_popup,
    answer_questions,
    choose_resume,
)
from config.settings import (
    COOKIES_PATH,
    LOCAL_STORAGE_PATH,
    LOGIN_PAGE,
    SEARCH_LINK,
    USERNAME,
    PASSWORD,
    JOB_SEARCH_QUERY,
    ADVANCED_SEARCH_URL_QUERY,
)


def main():
    global COUNTER
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    options.use_chromium = True
    options.add_argument("start-maximized")
    options.page_load_strategy = "eager"
    options.add_experimental_option("detach", True)
    DRIVER = webdriver.Chrome(options=options)
    ACTION = ActionChains(DRIVER)
    WAIT = WebDriverWait(DRIVER, 10)

    check_cookies_and_login(
        DRIVER,
        LOGIN_PAGE,
        COOKIES_PATH,
        LOCAL_STORAGE_PATH,
        SEARCH_LINK,
        USERNAME,
        PASSWORD,
    )

    if ADVANCED_SEARCH_URL_QUERY:
        DRIVER.get(ADVANCED_SEARCH_URL_QUERY)
    else:
        advanced_search(DRIVER)

    while COUNTER < 200:
        if click_all_jobs_on_the_page(DRIVER, WAIT) == Status.FAILURE:
            pass


if __name__ == "__main__":
    main()
