class AiJury < Formula
  include Language::Python::Virtualenv

  desc "Cross-vendor multi-agent PR & code review jury"
  homepage "https://ai-jury.dev/"
  url "https://files.pythonhosted.org/packages/40/ec/7e2fcea099c1224674b1690e6466ccf1a4284b1c699198b5afdbf0bb33fb/ai_jury-1.14.2.tar.gz"
  sha256 "ab664fc770f0f4c56b2ca4ed171b2da5cf81721ac3250a0bb59115c30917f889"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "jury 1.14.2", shell_output("#{bin}/jury --version")
    assert_match "error: provide one of", shell_output("#{bin}/jury --mock 2>&1", 1)
  end
end
