"""Work with resume utils"""


def choose_resume(job_title, driver):
    try:
        default_button = driver.find_element(
            By.XPATH,
            f"//input[@id='{dict_resume.RESUME_CODES[f'{dict_resume.DEFAULT_RESUME}']}']",
        )
        driver.execute_script(
            "arguments[0].scrollIntoView();  arguments[0].click();", default_button
        )
        for resume, code in dict_resume.RESUME_CODES.items():
            resume_button = driver.find_element(By.XPATH, f"//input[@id='{code}']")
            driver.execute_script(
                "arguments[0].scrollIntoView(); arguments[0].click();", resume_button
            )
            if resume.lower() in job_title.lower():
                break
    except NoSuchElementException as e:
        print(f"Failed to choose resume: Element not found {e}")
        print("\n")


def fill_in_cover_letter(message, driver, wait):
    global COUNTER
    scroll_to_bottom(driver)
    try:
        cover_letter_button = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//button[@data-qa="vacancy-response-letter-toggle"]')
            )
        )
        driver.execute_script("arguments[0].click()", cover_letter_button)
        cover_letter_text = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    '//form[@action="/applicant/vacancy_response/edit_ajax"]/textarea',
                )
            )
        )
        set_value_with_event(cover_letter_text, message, driver)
        submit_button = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, '//button[@data-qa="vacancy-response-letter-submit"]')
            )
        )
        driver.execute_script(
            "arguments[0].scrollIntoView(); arguments[0].click()", submit_button
        )
        time.sleep(1)
        try:
            error = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, '//div[@class="bloko-translate-guard"]')
                )
            )
            if error:
                return Status.SUCCESS
        except Exception:
            pass
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//div[@data-qa="vacancy-response-letter-informer"]')
            )
        )
        COUNTER += 1
        return Status.SUCCESS
    except Exception:
        return Status.FAILURE
